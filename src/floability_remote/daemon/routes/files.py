"""Endpoints for retained files from completed remote runs."""

import shutil

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from ...application.operations import ClientContext
from ...application.operations import results
from ...application.runtime import ApplicationRuntime
from ..dependencies import get_client, get_runtime
from ..schemas import (
    FileListResponse,
    RunFileContentResponse,
    RunFilesDownloadRequest,
    RunFilesDownloadResponse,
)


router = APIRouter(prefix="/runs", tags=["files"])


@router.get("/{run_id}/files", response_model=FileListResponse)
def list_files(
    run_id: str,
    runtime: ApplicationRuntime = Depends(get_runtime),
    client: ClientContext = Depends(get_client),
) -> FileListResponse:
    return results.list_run_files(runtime, run_id, client)


@router.get(
    "/{run_id}/files/{file_id}/content", response_model=RunFileContentResponse
)
def read_file(
    run_id: str,
    file_id: str,
    runtime: ApplicationRuntime = Depends(get_runtime),
    client: ClientContext = Depends(get_client),
) -> RunFileContentResponse:
    return results.read_run_file(runtime, run_id, file_id, client)


@router.post("/{run_id}/files/download", response_model=RunFilesDownloadResponse)
def download_files(
    run_id: str,
    request: RunFilesDownloadRequest,
    runtime: ApplicationRuntime = Depends(get_runtime),
    client: ClientContext = Depends(get_client),
) -> RunFilesDownloadResponse:
    return results.download_run_files(runtime, run_id, request, client)


@router.get("/{run_id}/files/{file_id}/download", response_class=FileResponse)
def download_file(
    run_id: str,
    file_id: str,
    runtime: ApplicationRuntime = Depends(get_runtime),
    client: ClientContext = Depends(get_client),
) -> FileResponse:
    item, temporary = results.download_temporary(runtime, run_id, file_id, client)
    return FileResponse(
        temporary,
        filename=item.filename,
        background=BackgroundTask(shutil.rmtree, temporary.parent, ignore_errors=True),
    )
