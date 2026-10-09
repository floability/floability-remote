"""List and download retained files from completed remote runs."""

import shutil

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from ...connection import ConnectionConflict
from ...errors import RemoteRunError
from ...files import FileService
from ...runs import Run
from ..errors import ApiError
from ..schemas import FileListResponse, RemoteFileModel
from ..services import Services, get_services
from .runs import find_run


router = APIRouter(prefix="/runs", tags=["files"])


def _ready_run(services: Services, run_id: str) -> Run:
    run = find_run(services, run_id)
    if not run.finished:
        raise ApiError(
            409, "files_not_ready", "Files are available after the run stops."
        )
    if not run.result.get("run_dir"):
        raise ApiError(
            409,
            "files_unavailable",
            "The run ended before a remote run directory was created.",
        )
    return run


def _file_service(services: Services, run: Run) -> FileService:
    try:
        session = services.connections.session_for(run.config.connection.target)
    except ConnectionConflict as error:
        raise ApiError(409, "connection_required", str(error)) from error
    return FileService(session)


@router.get("/{run_id}/files", response_model=FileListResponse)
def list_files(
    run_id: str, services: Services = Depends(get_services)
) -> FileListResponse:
    run = _ready_run(services, run_id)
    try:
        inventory = _file_service(services, run).list_files(run.result["run_dir"])
    except RemoteRunError as error:
        raise ApiError(409, "files_unavailable", str(error)) from error
    return FileListResponse(
        run_id=run.id,
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


@router.get("/{run_id}/files/{file_id}/download", response_class=FileResponse)
def download_file(
    run_id: str,
    file_id: str,
    services: Services = Depends(get_services),
) -> FileResponse:
    run = _ready_run(services, run_id)
    try:
        item, temporary = _file_service(services, run).download_temporary(
            run.result["run_dir"], file_id
        )
    except RemoteRunError as error:
        raise ApiError(409, "download_unavailable", str(error)) from error
    return FileResponse(
        temporary,
        filename=item.filename,
        background=BackgroundTask(shutil.rmtree, temporary.parent, ignore_errors=True),
    )
