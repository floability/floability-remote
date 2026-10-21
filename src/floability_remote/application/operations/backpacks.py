"""Local backpack operations shared by CLI and daemon adapters."""

from pathlib import Path

from ...domain.errors import RemoteRunError
from ...services.backpacks import (
    CloneResult,
    clone,
    discard_changes,
    inspect,
    list_directories,
    open_local,
    provenance,
)
from ..errors import OperationError
from ..contracts import (
    BackpackBrowseResponse,
    BackpackCloneResponse,
    BackpackInspectionResponse,
    BackpackProvenanceModel,
    DirectoryModel,
)
from . import operation_context


def inspection(path: Path, runtime=None, client=None) -> BackpackInspectionResponse:
    with operation_context(
        runtime, client, "backpack.inspect", inputs={"path": str(path)}
    ):
        report = inspect(path)
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


def provenance_response(path: Path, runtime=None, client=None) -> BackpackProvenanceModel:
    with operation_context(
        runtime, client, "backpack.status", inputs={"path": str(path)}
    ):
        return BackpackProvenanceModel(**provenance(path).__dict__)


def clone_response(result: CloneResult) -> BackpackCloneResponse:
    return BackpackCloneResponse(
        path=result.path,
        inspection=inspection(Path(result.path)),
        provenance=BackpackProvenanceModel(**result.provenance.__dict__),
    )


def browse(path: str, runtime=None, client=None) -> BackpackBrowseResponse:
    with operation_context(runtime, client, "backpack.browse", inputs={"path": path}):
        try:
            listing = list_directories(path)
        except RemoteRunError as error:
            raise OperationError("browse_failed", str(error), status=422) from error
        return BackpackBrowseResponse(
            path=listing.path,
            parent=listing.parent,
            is_backpack=listing.is_backpack,
            directories=[
                DirectoryModel(name=item.name, path=item.path, is_backpack=item.is_backpack)
                for item in listing.directories
            ],
            truncated=listing.truncated,
        )


def clone_repository(
    repository: str, ref: str = "", destination: str = "", runtime=None, client=None
):
    with operation_context(
        runtime,
        client,
        "backpack.clone",
        inputs={"repository": repository, "ref": ref, "destination": destination},
    ):
        try:
            return clone_response(clone(repository, ref, destination))
        except RemoteRunError as error:
            raise OperationError("clone_failed", str(error), status=422) from error


def discard(path: str, runtime=None, client=None) -> BackpackProvenanceModel:
    with operation_context(runtime, client, "backpack.discard", inputs={"path": path}):
        try:
            return BackpackProvenanceModel(**discard_changes(path).__dict__)
        except RemoteRunError as error:
            raise OperationError("discard_failed", str(error), status=422) from error


def open_backpack(path: str, editor: str, runtime=None, client=None) -> None:
    if editor not in ("folder", "vscode"):
        raise OperationError("invalid_editor", "Editor must be folder or vscode.", status=422)
    with operation_context(
        runtime, client, "backpack.open", inputs={"path": path, "editor": editor}
    ):
        try:
            open_local(path, vscode=editor == "vscode")
        except RemoteRunError as error:
            raise OperationError("open_failed", str(error), status=422) from error
