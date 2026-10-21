"""Validate and manage background Floability deployments."""

from typing import Optional

from ...deployment.runs import Run, RunConflict, RunNotFound
from ...domain.config import RunConfig
from ...remote.connection import ConnectionConflict
from ..validation import run_config_issues, validate_run_config
from ..errors import OperationError
from ..contracts import (
    ConfirmationModel,
    IssueModel,
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


def get(runtime: ApplicationRuntime, run_id: str, client=None) -> RunResponse:
    run = find_run(runtime, run_id)
    with operation_context(
        runtime, client, "run.get", target=run.config.connection.target
    ) as audit:
        if audit is not None:
            audit.identifiers(run_id=run.id)
        return run_response(run)


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
) -> None:
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
