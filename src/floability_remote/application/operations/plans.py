"""Backpack inspection and deployment-plan operations."""

from dataclasses import replace

from ...domain.config import BackpackSource, ConnectionConfig, ValidationIssue
from ...domain.errors import RemoteRunError
from ...remote.connection import ConnectionConflict, ConnectionState
from ...services.cluster import ClusterService
from ..contracts import (
    BackpackFileListResponse,
    BackpackFileModel,
    BackpackFileResponse,
    BackpackInspectionResponse,
    BackpackSourceCheckResponse,
    BackpackSourceRequest,
    BackpackSourceResponse,
    DeploymentPlanRequest,
    DeploymentPlanResponse,
    IssueModel,
)
from ..errors import OperationError
from ..planning import PlanningError, SourceSnapshot
from ..reproduce import cli_command
from ..runtime import ApplicationRuntime
from ..validation import run_config_issues, validate_run_config
from . import ClientContext


def _inspection(snapshot: SourceSnapshot) -> BackpackInspectionResponse:
    report = snapshot.inspection
    return BackpackInspectionResponse(
        path=report.path,
        name=report.name,
        source_type=report.source_type,
        file_count=report.file_count,
        total_bytes=report.total_bytes,
        workflow_found=report.workflow_found,
        valid=report.valid,
        problems=list(report.problems),
        workflow_files=list(report.workflow_files),
    )


def _source_response(snapshot: SourceSnapshot) -> BackpackSourceResponse:
    return BackpackSourceResponse(
        source_id=snapshot.id,
        source_type=snapshot.requested.source_type,
        location=snapshot.requested.display_location,
        requested_ref=snapshot.requested.ref,
        resolved_ref=snapshot.effective.ref,
        commit=snapshot.commit,
        inspection=_inspection(snapshot),
        entrypoints=list(snapshot.entrypoints),
        created_at=snapshot.created_at,
    )


def inspect_source(
    runtime: ApplicationRuntime,
    request: BackpackSourceRequest,
    client: ClientContext,
) -> BackpackSourceResponse:
    source = BackpackSource(
        source_type=request.source_type,
        repository=request.repository,
        ref=request.ref,
        local_path=request.local_path,
    )
    with runtime.audit.operation(
        "backpack.inspect_source",
        client=client.kind,
        session_id=client.session_id,
        inputs={
            "source_type": source.source_type,
            "location": source.display_location,
            "ref": source.ref,
        },
    ):
        try:
            return _source_response(runtime.planning.inspect_source(source))
        except (PlanningError, RemoteRunError) as error:
            raise OperationError(
                "backpack_inspection_failed", str(error), status=422
            ) from error


def check_source(
    runtime: ApplicationRuntime, source_id: str, client: ClientContext
) -> BackpackSourceCheckResponse:
    with runtime.audit.operation(
        "backpack.check_source",
        client=client.kind,
        session_id=client.session_id,
        inputs={"source_id": source_id},
    ):
        try:
            fresh = runtime.planning.source_is_fresh(source_id)
        except PlanningError as error:
            raise OperationError("source_not_found", str(error), status=404) from error
        return BackpackSourceCheckResponse(
            source_id=source_id,
            fresh=fresh,
            message=(
                "Backpack source is unchanged."
                if fresh
                else "Backpack source changed or disappeared. Inspect it again."
            ),
        )


def list_source_files(
    runtime: ApplicationRuntime, source_id: str, client: ClientContext
) -> BackpackFileListResponse:
    with runtime.audit.operation(
        "backpack.list_files",
        client=client.kind,
        session_id=client.session_id,
        inputs={"source_id": source_id},
    ):
        try:
            files = runtime.planning.files(source_id)
        except RemoteRunError as error:
            raise OperationError(
                "source_unavailable", str(error), status=409
            ) from error
        return BackpackFileListResponse(
            source_id=source_id,
            files=[BackpackFileModel(path=item.path, size=item.size) for item in files],
        )


def read_source_file(
    runtime: ApplicationRuntime, source_id: str, path: str, client: ClientContext
) -> BackpackFileResponse:
    with runtime.audit.operation(
        "backpack.read_file",
        client=client.kind,
        session_id=client.session_id,
        inputs={"source_id": source_id, "path": path},
    ):
        try:
            content = runtime.planning.read_file(source_id, path)
        except (PlanningError, RemoteRunError) as error:
            raise OperationError("file_unavailable", str(error), status=422) from error
        return BackpackFileResponse(source_id=source_id, path=path, content=content)


def validate_plan(
    runtime: ApplicationRuntime,
    request: DeploymentPlanRequest,
    client: ClientContext,
) -> DeploymentPlanResponse:
    try:
        source = runtime.planning.source(request.source_id)
    except PlanningError as error:
        raise OperationError("source_not_found", str(error), status=404) from error

    connection = runtime.connections.snapshot()
    if (
        connection.state != ConnectionState.CONNECTED
        or not connection.target
        or connection.connected_at is None
    ):
        raise OperationError(
            "connection_required",
            "Connect to a remote host before validating a deployment.",
            status=409,
        )
    config = request.to_config(
        ConnectionConfig(
            target=connection.target,
            identity_file=connection.identity_file,
        ),
        source.effective,
    )
    issues = list(run_config_issues(config))

    if not runtime.planning.source_is_fresh(request.source_id):
        issues.append(
            ValidationIssue(
                "source_id",
                "Backpack source changed after inspection; inspect it again.",
            )
        )

    candidates = source.entrypoints
    if config.entrypoint and config.entrypoint not in source.inspection.workflow_files:
        issues.append(
            ValidationIssue(
                "entrypoint",
                "must name a file found inside the backpack's workflow/ directory.",
            )
        )
    elif not config.entrypoint:
        if len(candidates) == 1:
            config = replace(config, entrypoint=candidates[0])
        elif len(candidates) > 1:
            issues.append(
                ValidationIssue(
                    "entrypoint",
                    "is required because the backpack has multiple runnable files: "
                    + ", ".join(candidates),
                )
            )
        else:
            issues.append(
                ValidationIssue(
                    "entrypoint",
                    "is required because no .ipynb, .py, or .sh workflow file was found.",
                )
            )

    with runtime.audit.operation(
        "deployment.validate_plan",
        client=client.kind,
        session_id=client.session_id,
        target=connection.target,
        inputs={
            "source_id": request.source_id,
            "mode": request.mode,
            "batch_type": request.batch_type,
        },
    ) as audit:
        if not issues:
            try:
                session = runtime.connections.session_for(connection.target)
                report = ClusterService(session).check(
                    config.environment, config.base_dir
                )
            except (ConnectionConflict, RemoteRunError) as error:
                raise OperationError(
                    "remote_check_failed", str(error), status=422
                ) from error
            if report.probe.os_name != "Linux":
                issues.append(
                    ValidationIssue("connection.target", "must be a Linux remote host.")
                )
            if not report.probe.setsid_available:
                issues.append(
                    ValidationIssue(
                        "connection.target",
                        "does not provide the required setsid command.",
                    )
                )
            if source.effective.source_type == "git" and not report.probe.git_available:
                issues.append(
                    ValidationIssue(
                        "source_id",
                        "uses Git, but Git is not available on the remote host.",
                    )
                )
            if not report.probe.conda and not report.probe.downloader:
                issues.append(
                    ValidationIssue(
                        "environment.conda_executable",
                        "Conda is unavailable and neither curl nor wget can install Miniforge.",
                    )
                )
            if config.batch_type not in report.available_batch_types:
                issues.append(
                    ValidationIssue(
                        "batch_type",
                        f"is not available on this host; choose one of: "
                        f"{', '.join(report.available_batch_types)}.",
                    )
                )

        if issues:
            return DeploymentPlanResponse(
                valid=False,
                source_id=request.source_id,
                target=connection.target,
                mode=config.mode,
                batch_type=config.batch_type,
                entrypoint=config.entrypoint,
                source_commit=source.commit,
                issues=[IssueModel.from_issue(issue) for issue in issues],
            )

        checked = validate_run_config(config)
        plan = runtime.planning.create_plan(
            request.source_id, checked, connection.connected_at
        )
        audit.identifiers(plan_id=plan.id)
        return DeploymentPlanResponse(
            valid=True,
            plan_id=plan.id,
            source_id=request.source_id,
            target=connection.target,
            mode=checked.mode,
            batch_type=checked.batch_type,
            entrypoint=checked.entrypoint,
            source_commit=source.commit,
            issues=[],
            command=cli_command(checked),
            created_at=plan.created_at,
        )
