"""Endpoints for backpacks that live on the user's computer."""

import shutil
import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, Request, Response

from ...application.operations import ClientContext
from ...application.operations import backpacks
from ...application.runtime import ApplicationRuntime
from ...services.backpacks import MAX_BYTES
from ..dependencies import get_client, get_runtime
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
    return backpacks.inspection(path)


def provenance_response(path: Path) -> BackpackProvenanceModel:
    return backpacks.provenance_response(path)


@router.post("/inspect", response_model=BackpackInspectionResponse)
def inspect_path(
    body: BackpackPathRequest,
    runtime: ApplicationRuntime = Depends(get_runtime),
    client: ClientContext = Depends(get_client),
) -> BackpackInspectionResponse:
    return backpacks.inspection(Path(body.path).expanduser(), runtime, client)


@router.post("/browse", response_model=BackpackBrowseResponse)
def browse(
    body: BackpackBrowseRequest,
    runtime: ApplicationRuntime = Depends(get_runtime),
    client: ClientContext = Depends(get_client),
) -> BackpackBrowseResponse:
    """List subfolders on this computer so the page can pick a real folder path."""
    return backpacks.browse(body.path, runtime, client)


@router.post("/zip", response_model=BackpackInspectionResponse, status_code=201)
async def upload_zip(
    filename: str,
    request: Request,
    runtime: ApplicationRuntime = Depends(get_runtime),
    client: ClientContext = Depends(get_client),
) -> BackpackInspectionResponse:
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
        with runtime.audit.operation(
            "backpack.upload",
            client=client.kind,
            session_id=client.session_id,
            inputs={"filename": filename},
        ):
            with archive.open("xb") as output:
                async for chunk in request.stream():
                    written += len(chunk)
                    if written > MAX_BYTES:
                        raise ApiError(
                            413, "upload_too_large", "ZIP files may be at most 200 MB."
                        )
                    output.write(chunk)
            report = inspection_response(archive)
    except Exception:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    if not report.valid:
        shutil.rmtree(directory, ignore_errors=True)
    return report


@router.post("/clone", response_model=BackpackCloneResponse, status_code=201)
def clone_repository(
    body: BackpackCloneRequest,
    runtime: ApplicationRuntime = Depends(get_runtime),
    client: ClientContext = Depends(get_client),
) -> BackpackCloneResponse:
    return backpacks.clone_repository(
        body.repository, body.ref, body.destination, runtime, client
    )


@router.post("/status", response_model=BackpackProvenanceModel)
def local_status(
    body: BackpackPathRequest,
    runtime: ApplicationRuntime = Depends(get_runtime),
    client: ClientContext = Depends(get_client),
) -> BackpackProvenanceModel:
    return backpacks.provenance_response(Path(body.path).expanduser(), runtime, client)


@router.post("/discard", response_model=BackpackProvenanceModel)
def discard_local_changes(
    body: BackpackPathRequest,
    runtime: ApplicationRuntime = Depends(get_runtime),
    client: ClientContext = Depends(get_client),
) -> BackpackProvenanceModel:
    return backpacks.discard(body.path, runtime, client)


@router.post("/open", status_code=204)
def open_local_backpack(
    body: BackpackOpenRequest,
    runtime: ApplicationRuntime = Depends(get_runtime),
    client: ClientContext = Depends(get_client),
) -> Response:
    backpacks.open_backpack(body.path, body.editor, runtime, client)
    return Response(status_code=204)
