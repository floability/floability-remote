import asyncio
import json
import time

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import StreamingResponse

from ...cli import cli_command
from ...config import run_config_issues, validate_run_config
from ...connection import ConnectionConflict
from ...runs import Run, RunConflict, RunNotFound
from ..errors import ApiError
from ..schemas import (
    ConfirmationAnswer,
    ConfirmationModel,
    IssueModel,
    RunRequest,
    RunResponse,
    ValidationResponse,
)
from ..services import Services, get_services


router = APIRouter(prefix="/runs", tags=["runs"])

WEB_MODES = ("run", "execute")
STREAM_POLL_SECONDS = 0.2
STREAM_KEEPALIVE_SECONDS = 15


def run_response(run: Run) -> RunResponse:
    config = run.config
    confirmation = run.confirmation
    return RunResponse(
        id=run.id,
        mode=config.mode,
        state=run.state,
        target=config.connection.target,
        repository=config.backpack.repository,
        ref=config.backpack.ref,
        batch_type=config.batch_type,
        entrypoint=config.entrypoint,
        created_at=run.created_at,
        finished_at=run.finished_at,
        message=run.message,
        result=run.result,
        jupyter_url=run.jupyter_url,
        confirmation=(
            ConfirmationModel(
                id=confirmation.id, key=confirmation.key, message=confirmation.message
            )
            if confirmation
            else None
        ),
        cancel_requested=run.cancel_requested,
        event_count=run.event_count,
    )


def find_run(services: Services, run_id: str) -> Run:
    try:
        return services.runs.get(run_id)
    except RunNotFound as error:
        raise ApiError(404, "run_not_found", str(error)) from error


@router.post("/validate", response_model=ValidationResponse)
def validate_run(request: RunRequest) -> ValidationResponse:
    """Check a run configuration with the same rules the CLI applies."""
    config = request.to_config()
    issues = run_config_issues(config)
    if issues:
        return ValidationResponse(
            valid=False, issues=[IssueModel.from_issue(issue) for issue in issues]
        )
    return ValidationResponse(
        valid=True, issues=[], command=cli_command(validate_run_config(config))
    )


@router.post("", response_model=RunResponse, status_code=201)
def start_run(request: RunRequest, services: Services = Depends(get_services)) -> RunResponse:
    """Start a run on the open connection; follow it with `GET /runs/{id}/events`."""
    config = request.to_config()
    issues = run_config_issues(config)
    if issues:
        raise ApiError(
            422,
            "invalid_config",
            "The run configuration is not valid.",
            [IssueModel.from_issue(issue) for issue in issues],
        )
    if config.mode not in WEB_MODES:
        raise ApiError(
            400, "not_available", f"Mode {config.mode} is not available from the browser."
        )
    try:
        run = services.runs.start(validate_run_config(config))
    except (RunConflict, ConnectionConflict) as error:
        raise ApiError(409, "conflict", str(error)) from error
    return run_response(run)


@router.get("/current", response_model=RunResponse)
def current_run(services: Services = Depends(get_services)) -> RunResponse:
    """The active run, or the most recent one."""
    run = services.runs.latest()
    if run is None:
        raise ApiError(404, "run_not_found", "No run has been started.")
    return run_response(run)


@router.get("/{run_id}", response_model=RunResponse)
def get_run(run_id: str, services: Services = Depends(get_services)) -> RunResponse:
    return run_response(find_run(services, run_id))


@router.get("/{run_id}/events")
async def run_events(
    run_id: str,
    request: Request,
    after: int = -1,
    services: Services = Depends(get_services),
) -> StreamingResponse:
    """Server-Sent Events: every event from `after + 1` (or `Last-Event-ID + 1`).

    Each message has `id: <position>`, `event: run`, and `data: <Event JSON>`.
    The stream ends with `event: end` after the run's terminal event.
    """
    run = find_run(services, run_id)
    last_event_id = request.headers.get("last-event-id", "")
    start = int(last_event_id) + 1 if last_event_id.isdigit() else max(after + 1, 0)

    async def stream():
        index = start
        last_sent = time.monotonic()
        yield "retry: 2000\n\n"
        while True:
            for event in run.events_after(index):
                payload = json.dumps(event.to_dict(), default=str)
                yield f"id: {index}\nevent: run\ndata: {payload}\n\n"
                index += 1
                last_sent = time.monotonic()
            if run.finished and index >= run.event_count:
                yield "event: end\ndata: {}\n\n"
                return
            if services.shutting_down or await request.is_disconnected():
                return
            if time.monotonic() - last_sent > STREAM_KEEPALIVE_SECONDS:
                yield ": keepalive\n\n"
                last_sent = time.monotonic()
            await asyncio.sleep(STREAM_POLL_SECONDS)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.post("/{run_id}/cancel", response_model=RunResponse, status_code=202)
def cancel_run(run_id: str, services: Services = Depends(get_services)) -> RunResponse:
    """Request cancellation; remote cleanup is reported through run events."""
    find_run(services, run_id)
    return run_response(services.runs.cancel(run_id))


@router.post("/{run_id}/confirmations/{confirmation_id}", status_code=204)
def answer_confirmation(
    run_id: str,
    confirmation_id: str,
    body: ConfirmationAnswer,
    services: Services = Depends(get_services),
) -> Response:
    run = find_run(services, run_id)
    if not run.answer_confirmation(confirmation_id, body.approved):
        raise ApiError(
            404,
            "confirmation_not_found",
            "This question is no longer waiting for an answer.",
        )
    return Response(status_code=204)
