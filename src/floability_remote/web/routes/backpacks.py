"""Inspect and stage backpacks that live on the user's computer."""

import re
import shutil
import tempfile
import uuid
from pathlib import Path, PurePosixPath

from fastapi import APIRouter, Request, Response

from ...errors import RemoteRunError
from ...local_backpack import (
    MAX_BYTES,
    MAX_FILES,
    SKIPPED_NAMES,
    clone,
    discard_changes,
    inspect,
    open_local,
    provenance,
)
from ..errors import ApiError
from ..schemas import (
    BackpackInspectionResponse,
    BackpackCloneRequest,
    BackpackCloneResponse,
    BackpackOpenRequest,
    BackpackProvenanceModel,
    BackpackPathRequest,
    BackpackStagingResponse,
)


router = APIRouter(prefix="/backpacks", tags=["backpacks"])
STAGING_ROOT = Path(tempfile.gettempdir()) / "floability-remote-uploads"
UPLOAD_ID = re.compile(r"[0-9a-f]{32}")


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
    )


def provenance_response(path: Path) -> BackpackProvenanceModel:
    return BackpackProvenanceModel(**provenance(path).__dict__)


def staging_path(upload_id: str) -> Path:
    if not UPLOAD_ID.fullmatch(upload_id):
        raise ApiError(404, "upload_not_found", "The local backpack upload was not found.")
    path = STAGING_ROOT / upload_id
    if not path.is_dir():
        raise ApiError(404, "upload_not_found", "The local backpack upload was not found.")
    return path


def safe_relative_path(raw: str) -> PurePosixPath:
    normalized = raw.replace("\\", "/")
    path = PurePosixPath(normalized)
    if (
        not normalized
        or path.is_absolute()
        or ".." in path.parts
        or (len(normalized) >= 2 and normalized[0].isalpha() and normalized[1] == ":")
    ):
        raise ApiError(422, "unsafe_path", f"Unsafe local backpack path: {raw}")
    parts = tuple(part for part in path.parts if part not in ("", "."))
    if not parts or any(part in SKIPPED_NAMES for part in parts):
        raise ApiError(422, "skipped_path", f"This path is not included in backpacks: {raw}")
    return PurePosixPath(*parts)


@router.post("/inspect", response_model=BackpackInspectionResponse)
def inspect_path(body: BackpackPathRequest) -> BackpackInspectionResponse:
    return inspection_response(Path(body.path).expanduser())


@router.post("/staging", response_model=BackpackStagingResponse, status_code=201)
def create_staging() -> BackpackStagingResponse:
    STAGING_ROOT.mkdir(parents=True, exist_ok=True)
    upload_id = uuid.uuid4().hex
    path = STAGING_ROOT / upload_id
    path.mkdir(mode=0o700)
    return BackpackStagingResponse(upload_id=upload_id, path=str(path))


@router.put("/staging/{upload_id}/files", status_code=204)
async def upload_file(upload_id: str, path: str, request: Request) -> Response:
    root = staging_path(upload_id)
    relative = safe_relative_path(path)
    existing_files = sum(1 for item in root.rglob("*") if item.is_file())
    if existing_files >= MAX_FILES:
        raise ApiError(413, "too_many_files", f"Backpacks may contain at most {MAX_FILES:,} files.")
    total = sum(item.stat().st_size for item in root.rglob("*") if item.is_file())
    destination = root.joinpath(*relative.parts)
    destination.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    try:
        with destination.open("xb") as output:
            async for chunk in request.stream():
                written += len(chunk)
                if total + written > MAX_BYTES:
                    raise ApiError(413, "upload_too_large", "Backpacks may be at most 200 MB.")
                output.write(chunk)
    except FileExistsError as error:
        raise ApiError(409, "duplicate_path", f"Duplicate backpack path: {path}") from error
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return Response(status_code=204)


@router.post("/staging/{upload_id}/complete", response_model=BackpackInspectionResponse)
def complete_staging(upload_id: str) -> BackpackInspectionResponse:
    return inspection_response(staging_path(upload_id))


@router.post("/zip", response_model=BackpackInspectionResponse, status_code=201)
async def upload_zip(filename: str, request: Request) -> BackpackInspectionResponse:
    if not filename.lower().endswith(".zip"):
        raise ApiError(422, "not_zip", "Choose a ZIP file.")
    STAGING_ROOT.mkdir(parents=True, exist_ok=True)
    upload_id = uuid.uuid4().hex
    directory = STAGING_ROOT / upload_id
    directory.mkdir(mode=0o700)
    destination = directory / "backpack.zip"
    written = 0
    try:
        with destination.open("xb") as output:
            async for chunk in request.stream():
                written += len(chunk)
                if written > MAX_BYTES:
                    raise ApiError(413, "upload_too_large", "ZIP files may be at most 200 MB.")
                output.write(chunk)
        return inspection_response(destination)
    except Exception:
        shutil.rmtree(directory, ignore_errors=True)
        raise


@router.post("/clone", response_model=BackpackCloneResponse, status_code=201)
def clone_repository(body: BackpackCloneRequest) -> BackpackCloneResponse:
    try:
        result = clone(body.repository, body.ref, body.destination)
    except RemoteRunError as error:
        raise ApiError(422, "clone_failed", str(error)) from error
    path = Path(result.path)
    return BackpackCloneResponse(
        path=result.path,
        inspection=inspection_response(path),
        provenance=BackpackProvenanceModel(**result.provenance.__dict__),
    )


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
