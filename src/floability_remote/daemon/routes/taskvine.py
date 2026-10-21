"""Endpoint for live TaskVine activity for one run."""

from fastapi import APIRouter, Depends

from ...application.operations import ClientContext
from ...application.operations import results
from ...application.runtime import ApplicationRuntime
from ..dependencies import get_client, get_runtime
from ..schemas import TaskVineStatusResponse


router = APIRouter(prefix="/runs", tags=["taskvine"])


@router.get("/{run_id}/taskvine", response_model=TaskVineStatusResponse)
def taskvine_status(
    run_id: str,
    runtime: ApplicationRuntime = Depends(get_runtime),
    client: ClientContext = Depends(get_client),
) -> TaskVineStatusResponse:
    """Summarize the run's TaskVine transactions log and worker factory output."""
    return results.taskvine_status(runtime, run_id, client)
