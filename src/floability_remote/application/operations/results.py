"""Retained-file and TaskVine operations for completed or active runs."""

import time
from dataclasses import asdict

from ...domain.errors import RemoteRunError
from ...remote.connection import ConnectionConflict
from ...services.files import FileService
from ...services.taskvine import TaskVineService
from ..errors import OperationError
from ..contracts import FileListResponse, RemoteFileModel, TaskVineStatusResponse
from ..runtime import ApplicationRuntime
from . import ClientContext
from .deployments import find_run


def list_files_with_session(session, run_dir: str, service=None):
    """Inventory a known run directory using an existing SSH session."""
    return (service or FileService(session)).list_files(run_dir)


def download_with_session(session, run_dir: str, file_id: str, destination, service=None):
    """Download one retained file using an existing SSH session."""
    return (service or FileService(session)).download(run_dir, file_id, destination)


def taskvine_with_session(session, run_dir: str):
    """Read TaskVine status using an existing SSH session."""
    return TaskVineService(session).status(run_dir)


def _session(runtime: ApplicationRuntime, target: str):
    try:
        return runtime.connections.session_for(target)
    except ConnectionConflict as error:
        raise OperationError("connection_required", str(error), status=409) from error


def _ready_run(runtime: ApplicationRuntime, run_id: str):
    run = find_run(runtime, run_id)
    if not run.finished:
        raise OperationError("files_not_ready", "Files are available after the run stops.", status=409)
    if not run.result.get("run_dir"):
        raise OperationError(
            "files_unavailable",
            "The run ended before a remote run directory was created.",
            status=409,
        )
    return run


def inventory_response(run_id: str, inventory) -> FileListResponse:
    return FileListResponse(
        run_id=run_id,
        instance_found=inventory.instance_found,
        truncated=inventory.truncated,
        files=[
            RemoteFileModel(
                id=item.id,
                group=item.group,
                path=item.path,
                size=item.size,
                downloadable=item.downloadable,
                reason=item.reason,
            )
            for item in inventory.files
        ],
    )


def list_run_files(runtime: ApplicationRuntime, run_id: str, client: ClientContext):
    run = _ready_run(runtime, run_id)
    with runtime.audit.operation(
        "results.list_files",
        client=client.kind,
        session_id=client.session_id,
        target=run.config.connection.target,
    ) as audit:
        audit.identifiers(run_id=run.id)
        try:
            inventory = list_files_with_session(
                _session(runtime, run.config.connection.target), run.result["run_dir"]
            )
        except RemoteRunError as error:
            raise OperationError("files_unavailable", str(error), status=409) from error
        return inventory_response(run.id, inventory)


def download_temporary(
    runtime: ApplicationRuntime,
    run_id: str,
    file_id: str,
    client: ClientContext,
):
    run = _ready_run(runtime, run_id)
    with runtime.audit.operation(
        "results.download",
        client=client.kind,
        session_id=client.session_id,
        target=run.config.connection.target,
        inputs={"file_id": file_id},
    ) as audit:
        audit.identifiers(run_id=run.id)
        try:
            return FileService(_session(runtime, run.config.connection.target)).download_temporary(
                run.result["run_dir"], file_id
            )
        except RemoteRunError as error:
            raise OperationError("download_unavailable", str(error), status=409) from error


def taskvine_status(runtime: ApplicationRuntime, run_id: str, client: ClientContext):
    run = find_run(runtime, run_id)
    checked_at = time.time()
    if not run.remote_run_dir:
        return TaskVineStatusResponse(run_id=run.id, available=False, checked_at=checked_at)
    with runtime.audit.operation(
        "run.taskvine_status",
        client=client.kind,
        session_id=client.session_id,
        target=run.config.connection.target,
    ) as audit:
        audit.identifiers(run_id=run.id)
        try:
            status = taskvine_with_session(
                _session(runtime, run.config.connection.target), run.remote_run_dir
            )
        except RemoteRunError as error:
            raise OperationError("taskvine_unavailable", str(error), status=502) from error
        return TaskVineStatusResponse(
            run_id=run.id,
            available=True,
            checked_at=checked_at,
            uses_taskvine=status.uses_taskvine,
            **asdict(status),
        )
