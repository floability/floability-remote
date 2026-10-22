"""Deployment lifecycle endpoints."""

import asyncio
import json
import time

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import StreamingResponse

from ...application.operations import ClientContext
from ...application.operations import deployments
from ...application.runtime import ApplicationRuntime
from ...deployment.runs import Run
from ..schemas import (
    ConfirmationAnswer,
    InteractiveSessionResponse,
    InteractiveUrlResponse,
    RunEventPageResponse,
    RunRequest,
    RunResponse,
    ValidationResponse,
)
from ..dependencies import get_client, get_runtime


router = APIRouter(prefix="/runs", tags=["runs"])

STREAM_POLL_SECONDS = 0.2
STREAM_KEEPALIVE_SECONDS = 15


def run_response(run: Run) -> RunResponse:
    return deployments.run_response(run)


def find_run(runtime: ApplicationRuntime, run_id: str) -> Run:
    return deployments.find_run(runtime, run_id)


@router.post("/validate", response_model=ValidationResponse)
def validate_run(
    request: RunRequest,
    runtime: ApplicationRuntime = Depends(get_runtime),
    client: ClientContext = Depends(get_client),
) -> ValidationResponse:
    """Check a run configuration with the same rules the CLI applies."""
    return deployments.validate(request.to_config(), runtime, client)


@router.post("", response_model=RunResponse, status_code=201)
def start_run(
    request: RunRequest,
    runtime: ApplicationRuntime = Depends(get_runtime),
    client: ClientContext = Depends(get_client),
) -> RunResponse:
    """Start a run on the open connection; follow it with `GET /runs/{id}/events`."""
    return deployments.start(runtime, request.to_config(), client)


@router.get("/current", response_model=RunResponse)
def current_run(
    runtime: ApplicationRuntime = Depends(get_runtime),
    client: ClientContext = Depends(get_client),
) -> RunResponse:
    """The active run, or the most recent one."""
    return deployments.latest(runtime, client)


@router.get("/{run_id}", response_model=RunResponse)
def get_run(
    run_id: str,
    runtime: ApplicationRuntime = Depends(get_runtime),
    client: ClientContext = Depends(get_client),
) -> RunResponse:
    return deployments.get(runtime, run_id, client)


@router.get("/{run_id}/interactive", response_model=InteractiveSessionResponse)
def get_interactive_session(
    run_id: str,
    runtime: ApplicationRuntime = Depends(get_runtime),
    client: ClientContext = Depends(get_client),
) -> InteractiveSessionResponse:
    """Return interactive readiness and tunnel state without the Jupyter token."""
    return deployments.interactive_session(runtime, run_id, client)


@router.get("/{run_id}/interactive/url", response_model=InteractiveUrlResponse)
def get_interactive_url(
    run_id: str,
    runtime: ApplicationRuntime = Depends(get_runtime),
    client: ClientContext = Depends(get_client),
) -> InteractiveUrlResponse:
    """Return the token-bearing URL to a trusted authenticated local client."""
    return deployments.interactive_url(runtime, run_id, client)


@router.get("/{run_id}/event-page", response_model=RunEventPageResponse)
def run_event_page(
    run_id: str,
    cursor: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    include_logs: bool = False,
    runtime: ApplicationRuntime = Depends(get_runtime),
    client: ClientContext = Depends(get_client),
) -> RunEventPageResponse:
    """Return a bounded page of retained, redacted run events."""
    return deployments.events(runtime, run_id, cursor, limit, include_logs, client)


@router.get("/{run_id}/wait", response_model=RunEventPageResponse)
def wait_for_run(
    run_id: str,
    cursor: int = Query(0, ge=0),
    timeout_seconds: float = Query(10, ge=0, le=30),
    limit: int = Query(50, ge=1, le=200),
    include_logs: bool = False,
    runtime: ApplicationRuntime = Depends(get_runtime),
    client: ClientContext = Depends(get_client),
) -> RunEventPageResponse:
    """Wait at most 30 seconds for progress, a question, or completion."""
    return deployments.wait(
        runtime,
        run_id,
        cursor,
        timeout_seconds,
        limit,
        include_logs,
        client,
    )


@router.get("/{run_id}/events")
async def run_events(
    run_id: str,
    request: Request,
    after: int = -1,
    runtime: ApplicationRuntime = Depends(get_runtime),
    client: ClientContext = Depends(get_client),
) -> StreamingResponse:
    """Server-Sent Events: every event from `after + 1` (or `Last-Event-ID + 1`).

    Each message has `id: <position>`, `event: run`, and `data: <Event JSON>`.
    The stream ends with `event: end` after the run's terminal event.
    """
    run = find_run(runtime, run_id)
    with runtime.audit.operation(
        "run.events",
        client=client.kind,
        session_id=client.session_id,
        target=run.config.connection.target,
    ) as audit:
        audit.identifiers(run_id=run.id)
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
            if runtime.shutting_down or await request.is_disconnected():
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
def cancel_run(
    run_id: str,
    runtime: ApplicationRuntime = Depends(get_runtime),
    client: ClientContext = Depends(get_client),
) -> RunResponse:
    """Request cancellation; remote cleanup is reported through run events."""
    return deployments.cancel(runtime, run_id, client)


@router.post("/{run_id}/confirmations/{confirmation_id}", status_code=204)
def answer_confirmation(
    run_id: str,
    confirmation_id: str,
    body: ConfirmationAnswer,
    runtime: ApplicationRuntime = Depends(get_runtime),
    client: ClientContext = Depends(get_client),
) -> Response:
    deployments.answer_confirmation(
        runtime, run_id, confirmation_id, body.approved, client
    )
    return Response(status_code=204)
