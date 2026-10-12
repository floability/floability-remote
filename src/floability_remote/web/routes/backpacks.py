"""Find, clone, and inspect backpacks that live on the user's computer."""

import shutil
import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, Request, Response

from ...errors import RemoteRunError
from ...local_backpack import (
    MAX_BYTES,
    CloneResult,
    clone,
    discard_changes,
    inspect,
    list_directories,
    open_local,
    provenance,
)
from ..errors import ApiError
from ..schemas import (
    BackpackBrowseRequest,
    BackpackBrowseResponse,
    BackpackCloneRequest,
    BackpackCloneResponse,
    BackpackInspectionResponse,
    BackpackOpenRequest,
    BackpackPathRequest,
    BackpackProvenanceModel,
    DirectoryModel,
)


router = APIRouter(prefix="/backpacks", tags=["backpacks"])

# Uploaded ZIPs are kept here, unchanged, because a browser cannot reveal the
# file's own path; runs read the archive from this copy.
UPLOAD_ROOT = Path(tempfile.gettempdir()) / "floability-remote-uploads"


def _upload_name(filename: str) -> str:
    name = "".join(
        character if character.isalnum() or character in "._-" else "-"
        for character in Path(filename).name
    ).strip(".-")
    return name if name.lower().endswith(".zip") else "backpack.zip"


def inspection_response(path: Path) -> BackpackInspectionResponse:
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


def provenance_response(path: Path) -> BackpackProvenanceModel:
    return BackpackProvenanceModel(**provenance(path).__dict__)


def copy_response(result: CloneResult) -> BackpackCloneResponse:
    return BackpackCloneResponse(
        path=result.path,
        inspection=inspection_response(Path(result.path)),
        provenance=BackpackProvenanceModel(**result.provenance.__dict__),
    )


@router.post("/inspect", response_model=BackpackInspectionResponse)
def inspect_path(body: BackpackPathRequest) -> BackpackInspectionResponse:
    return inspection_response(Path(body.path).expanduser())


@router.post("/browse", response_model=BackpackBrowseResponse)
def browse(body: BackpackBrowseRequest) -> BackpackBrowseResponse:
    """List subfolders on this computer so the page can pick a real folder path."""
    try:
        listing = list_directories(body.path)
    except RemoteRunError as error:
        raise ApiError(422, "browse_failed", str(error)) from error
    return BackpackBrowseResponse(
        path=listing.path,
        parent=listing.parent,
        is_backpack=listing.is_backpack,
        directories=[
            DirectoryModel(name=entry.name, path=entry.path, is_backpack=entry.is_backpack)
            for entry in listing.directories
        ],
        truncated=listing.truncated,
    )


@router.post("/zip", response_model=BackpackInspectionResponse, status_code=201)
async def upload_zip(filename: str, request: Request) -> BackpackInspectionResponse:
    """Keep an uploaded ZIP unchanged and return its inspection.

    Runs upload the archive's files as they are; nothing is extracted on this
    computer. Invalid archives are not kept.
    """
    if not filename.lower().endswith(".zip"):
        raise ApiError(422, "not_zip", "Choose a ZIP file.")
    UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    directory = UPLOAD_ROOT / uuid.uuid4().hex
    directory.mkdir(mode=0o700)
    archive = directory / _upload_name(filename)
    written = 0
    try:
        with archive.open("xb") as output:
            async for chunk in request.stream():
                written += len(chunk)
                if written > MAX_BYTES:
                    raise ApiError(413, "upload_too_large", "ZIP files may be at most 200 MB.")
                output.write(chunk)
        report = inspection_response(archive)
    except Exception:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    if not report.valid:
        shutil.rmtree(directory, ignore_errors=True)
    return report


@router.post("/clone", response_model=BackpackCloneResponse, status_code=201)
def clone_repository(body: BackpackCloneRequest) -> BackpackCloneResponse:
    try:
        result = clone(body.repository, body.ref, body.destination)
    except RemoteRunError as error:
        raise ApiError(422, "clone_failed", str(error)) from error
    return copy_response(result)


@router.post("/status", response_model=BackpackProvenanceModel)
def local_status(body: BackpackPathRequest) -> BackpackProvenanceModel:
    return provenance_response(Path(body.path).expanduser())


@router.post("/discard", response_model=BackpackProvenanceModel)
def discard_local_changes(body: BackpackPathRequest) -> BackpackProvenanceModel:
    try:
        return BackpackProvenanceModel(**discard_changes(body.path).__dict__)
    except RemoteRunError as error:
        raise ApiError(422, "discard_failed", str(error)) from error


@router.post("/open", status_code=204)
def open_local_backpack(body: BackpackOpenRequest) -> Response:
    if body.editor not in ("folder", "vscode"):
        raise ApiError(422, "invalid_editor", "Editor must be folder or vscode.")
    try:
        open_local(body.path, vscode=body.editor == "vscode")
    except RemoteRunError as error:
        raise ApiError(422, "open_failed", str(error)) from error
    return Response(status_code=204)
