"""Validate and manage background Floability deployments."""

import time
from typing import Optional

from ...deployment.runs import Run, RunConflict, RunNotFound
from ...domain.config import RunConfig
from ...domain.errors import RemoteRunError
from ...remote.connection import ConnectionConflict, ConnectionState
from ..validation import run_config_issues, validate_run_config
from ..errors import OperationError
from ..contracts import (
    ConfirmationModel,
    InteractiveSessionResponse,
    InteractiveUrlResponse,
    IndexedEventModel,
    IssueModel,
    RunEventPageResponse,
    RunResponse,
    ValidationResponse,
)
from ..reproduce import cli_command
from ..runtime import ApplicationRuntime
from . import ClientContext, operation_context


ALLOWED_MODES = ("run", "execute")


def run_response(run: Run) -> RunResponse:
    config = run.config
    confirmation = run.confirmation
    return RunResponse(
        id=run.id,
        mode=config.mode,
        state=run.state,
        target=config.connection.target,
        repository=config.backpack.repository,
        source_type=config.backpack.source_type,
        local_path=config.backpack.local_path,
        ref=config.backpack.ref,
        batch_type=config.batch_type,
        entrypoint=config.entrypoint,
        created_at=run.created_at,
        finished_at=run.finished_at,
        message=run.message,
        result=run.result,
        remote_run_dir=run.remote_run_dir,
        command_log=(
            run.result.get("log_path")
            or (
                f"{run.remote_run_dir}/{config.mode}-command.log"
                if run.remote_run_dir
                else None
            )
        ),
        jupyter_url=run.jupyter_url,
        confirmation=(
            ConfirmationModel(
                id=confirmation.id,
                key=confirmation.key,
                message=confirmation.message,
            )
            if confirmation
            else None
        ),
        cancel_requested=run.cancel_requested,
        event_count=run.event_count,
    )


def find_run(runtime: ApplicationRuntime, run_id: str) -> Run:
    try:
        return runtime.runs.get(run_id)
    except RunNotFound as error:
        raise OperationError("run_not_found", str(error), status=404) from error


def validate(
    config: RunConfig, runtime: Optional[ApplicationRuntime] = None, client=None
) -> ValidationResponse:
    with operation_context(
        runtime,
        client,
        "run.validate",
        target=config.connection.target,
        inputs={"mode": config.mode, "batch_type": config.batch_type},
    ):
        issues = run_config_issues(config)
        if issues:
            return ValidationResponse(
                valid=False,
                issues=[IssueModel.from_issue(issue) for issue in issues],
            )
        checked = validate_run_config(config)
        return ValidationResponse(valid=True, issues=[], command=cli_command(checked))


def start(
    runtime: ApplicationRuntime,
    config: RunConfig,
    client: ClientContext,
) -> RunResponse:
    issues = run_config_issues(config)
    if issues:
        raise OperationError(
            "invalid_config",
            "The run configuration is not valid.",
            status=422,
            issues=[IssueModel.from_issue(issue).model_dump() for issue in issues],
        )
    if config.mode not in ALLOWED_MODES:
        raise OperationError(
            "not_available",
            f"Mode {config.mode} is not available from the browser.",
            status=400,
        )
    with runtime.audit.operation(
        "run.start",
        client=client.kind,
        session_id=client.session_id,
        target=config.connection.target,
        inputs={
            "mode": config.mode,
            "batch_type": config.batch_type,
            "backpack": config.backpack.display_location,
        },
    ) as audit:
        try:
            run = runtime.runs.start(validate_run_config(config))
        except (RunConflict, ConnectionConflict) as error:
            raise OperationError("conflict", str(error), status=409) from error
        audit.identifiers(run_id=run.id)
        return run_response(run)


def start_plan(
    runtime: ApplicationRuntime,
    plan_id: str,
    client: ClientContext,
) -> RunResponse:
    """Start one deployment from a fresh, validated execute or run plan."""
    connection = runtime.connections.snapshot()
    if (
        connection.state != ConnectionState.CONNECTED
        or not connection.target
        or connection.connected_at is None
    ):
        raise OperationError(
            "connection_required",
            "Connect to the validated remote host before starting the deployment.",
            status=409,
        )
    try:
        plan = runtime.planning.require_fresh_plan(
            plan_id, connection.target, connection.connected_at
        )
    except RemoteRunError as error:
        raise OperationError("stale_plan", str(error), status=409) from error
    if plan.config.mode not in ALLOWED_MODES:
        raise OperationError(
            "unsupported_mode",
            f"Mode {plan.config.mode} is not available for deployment.",
            status=400,
        )
    with runtime.audit.operation(
        "deployment.start_plan",
        client=client.kind,
        session_id=client.session_id,
        target=connection.target,
        inputs={"plan_id": plan.id, "source_id": plan.source_id},
    ) as audit:
        try:
            run = runtime.runs.start(plan.config)
        except (RunConflict, ConnectionConflict) as error:
            raise OperationError("conflict", str(error), status=409) from error
        audit.identifiers(plan_id=plan.id, run_id=run.id)
        return run_response(run)


def _interactive_run(runtime: ApplicationRuntime, run_id: str) -> Run:
    run = find_run(runtime, run_id)
    if run.config.mode != "run":
        raise OperationError(
            "not_interactive",
            f"Run {run_id} is an execute deployment, not an interactive session.",
            status=409,
        )
    return run


def interactive_session(
    runtime: ApplicationRuntime,
    run_id: str,
    client: ClientContext,
) -> InteractiveSessionResponse:
    """Return model-safe interactive and tunnel state without the Jupyter token."""
    run = _interactive_run(runtime, run_id)
    if run.state == "failed":
        state = "failed"
    elif run.finished:
        state = "stopped"
    elif run.cancel_requested:
        state = "stopping"
    elif run.jupyter_url:
        state = "ready"
    else:
        state = "starting"

    messages = {
        "starting": "The interactive session is starting.",
        "ready": "Jupyter is ready through the daemon-owned SSH tunnel.",
        "stopping": "The interactive session is stopping and cleaning up.",
        "stopped": run.message or "The interactive session has stopped.",
        "failed": run.message or "The interactive session failed.",
    }
    with runtime.audit.operation(
        "run.interactive_session",
        client=client.kind,
        session_id=client.session_id,
        target=run.config.connection.target,
    ) as audit:
        audit.identifiers(run_id=run.id)
        return InteractiveSessionResponse(
            run_id=run.id,
            state=state,
            run_state=run.state,
            tunnel_ready=bool(run.jupyter_url),
            local_port=run.jupyter_local_port,
            remote_port=run.jupyter_remote_port,
            url_available=bool(run.jupyter_url),
            message=messages[state],
            cancel_requested=run.cancel_requested,
        )


def interactive_url(
    runtime: ApplicationRuntime,
    run_id: str,
    client: ClientContext,
) -> InteractiveUrlResponse:
    """Return the token-bearing URL only to a trusted local daemon client."""
    run = _interactive_run(runtime, run_id)
    with runtime.audit.operation(
        "run.interactive_url",
        client=client.kind,
        session_id=client.session_id,
        target=run.config.connection.target,
    ) as audit:
        audit.identifiers(run_id=run.id)
        if not run.jupyter_url:
            raise OperationError(
                "interactive_not_ready",
                "The interactive session does not currently have a working Jupyter URL.",
                status=409,
            )
        return InteractiveUrlResponse(run_id=run.id, url=run.jupyter_url)


def get(runtime: ApplicationRuntime, run_id: str, client=None) -> RunResponse:
    run = find_run(runtime, run_id)
    with operation_context(
        runtime, client, "run.get", target=run.config.connection.target
    ) as audit:
        if audit is not None:
            audit.identifiers(run_id=run.id)
        return run_response(run)


def _event_page(
    run: Run,
    cursor: int,
    limit: int,
    include_logs: bool,
    *,
    timed_out: bool = False,
) -> RunEventPageResponse:
    cursor = min(max(cursor, 0), run.event_count)
    limit = max(1, min(limit, 200))
    selected = []
    next_cursor = cursor
    for index, event in enumerate(run.events_after(cursor), start=cursor):
        next_cursor = index + 1
        if include_logs or event.kind != "log":
            selected.append(IndexedEventModel(cursor=index, **event.to_dict()))
            if len(selected) >= limit:
                break
    event_count = run.event_count
    has_more = next_cursor < event_count
    return RunEventPageResponse(
        run=run_response(run),
        events=selected,
        next_cursor=next_cursor,
        has_more=has_more,
        terminal=run.finished and not has_more,
        timed_out=timed_out,
    )


def events(
    runtime: ApplicationRuntime,
    run_id: str,
    cursor: int,
    limit: int,
    include_logs: bool,
    client: ClientContext,
) -> RunEventPageResponse:
    run = find_run(runtime, run_id)
    with runtime.audit.operation(
        "run.get_events",
        client=client.kind,
        session_id=client.session_id,
        target=run.config.connection.target,
        inputs={"cursor": cursor, "limit": limit, "include_logs": include_logs},
    ) as audit:
        audit.identifiers(run_id=run.id)
        return _event_page(run, cursor, limit, include_logs)


def wait(
    runtime: ApplicationRuntime,
    run_id: str,
    cursor: int,
    timeout_seconds: float,
    limit: int,
    include_logs: bool,
    client: ClientContext,
) -> RunEventPageResponse:
    """Wait a bounded time for visible progress, confirmation, or completion."""
    run = find_run(runtime, run_id)
    timeout_seconds = max(0.0, min(timeout_seconds, 30.0))
    deadline = time.monotonic() + timeout_seconds
    with runtime.audit.operation(
        "run.wait",
        client=client.kind,
        session_id=client.session_id,
        target=run.config.connection.target,
        inputs={
            "cursor": cursor,
            "timeout_seconds": timeout_seconds,
            "limit": limit,
            "include_logs": include_logs,
        },
    ) as audit:
        audit.identifiers(run_id=run.id)
        current = max(cursor, 0)
        while True:
            page = _event_page(run, current, limit, include_logs)
            if page.events or page.terminal or page.has_more:
                return page
            current = page.next_cursor
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return _event_page(run, current, limit, include_logs, timed_out=True)
            run.wait_for_events(current, remaining)


def latest(runtime: ApplicationRuntime, client=None) -> RunResponse:
    run = runtime.runs.latest()
    if run is None:
        raise OperationError("run_not_found", "No run has been started.", status=404)
    with operation_context(
        runtime, client, "run.get", target=run.config.connection.target
    ) as audit:
        if audit is not None:
            audit.identifiers(run_id=run.id)
        return run_response(run)


def cancel(
    runtime: ApplicationRuntime,
    run_id: str,
    client: ClientContext,
) -> RunResponse:
    run = find_run(runtime, run_id)
    with runtime.audit.operation(
        "run.cancel",
        client=client.kind,
        session_id=client.session_id,
        target=run.config.connection.target,
    ) as audit:
        audit.identifiers(run_id=run.id)
        return run_response(runtime.runs.cancel(run.id))


def answer_confirmation(
    runtime: ApplicationRuntime,
    run_id: str,
    confirmation_id: str,
    approved: bool,
    client: ClientContext,
) -> RunResponse:
    run = find_run(runtime, run_id)
    with runtime.audit.operation(
        "run.answer_confirmation",
        client=client.kind,
        session_id=client.session_id,
        target=run.config.connection.target,
        inputs={"confirmation_id": confirmation_id, "approved": approved},
    ) as audit:
        audit.identifiers(run_id=run.id)
        if not run.answer_confirmation(confirmation_id, approved):
            raise OperationError(
                "confirmation_not_found",
                "This question is no longer waiting for an answer.",
                status=404,
            )
        return run_response(run)
