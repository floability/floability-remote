"""Live TaskVine activity (manager, workers, tasks) for one run."""

import time
from dataclasses import asdict

from fastapi import APIRouter, Depends

from ...connection import ConnectionConflict
from ...errors import RemoteRunError
from ...taskvine import TaskVineService
from ..errors import ApiError
from ..schemas import TaskVineStatusResponse
from ..services import Services, get_services
from .runs import find_run


router = APIRouter(prefix="/runs", tags=["taskvine"])


@router.get("/{run_id}/taskvine", response_model=TaskVineStatusResponse)
def taskvine_status(
    run_id: str, services: Services = Depends(get_services)
) -> TaskVineStatusResponse:
    """Summarize the run's TaskVine transactions log and worker factory output."""
    run = find_run(services, run_id)
    checked_at = time.time()
    if not run.remote_run_dir:
        return TaskVineStatusResponse(run_id=run.id, available=False, checked_at=checked_at)
    try:
        session = services.connections.session_for(run.config.connection.target)
    except ConnectionConflict as error:
        raise ApiError(409, "connection_required", str(error)) from error
    try:
        status = TaskVineService(session).status(run.remote_run_dir)
    except RemoteRunError as error:
        raise ApiError(502, "taskvine_unavailable", str(error)) from error
    return TaskVineStatusResponse(
        run_id=run.id,
        available=True,
        checked_at=checked_at,
        uses_taskvine=status.uses_taskvine,
        **asdict(status),
    )
