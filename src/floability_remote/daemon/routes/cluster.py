"""Read-only readiness endpoints for the connected login node."""

from fastapi import APIRouter, Depends

from ...application.operations import ClientContext
from ...application.operations import clusters
from ...application.runtime import ApplicationRuntime
from ...domain.config import EnvironmentConfig
from ..schemas import ClusterCheckRequest, ClusterCheckResponse
from ..dependencies import get_client, get_runtime


router = APIRouter(prefix="/cluster", tags=["cluster"])


@router.post("/check", response_model=ClusterCheckResponse)
def check_cluster(
    request: ClusterCheckRequest,
    runtime: ApplicationRuntime = Depends(get_runtime),
    client: ClientContext = Depends(get_client),
) -> ClusterCheckResponse:
    environment = EnvironmentConfig(
        env_name=request.env_name,
        floability_version=request.floability_version,
        conda_executable=request.conda_executable,
    )
    return clusters.check(
        runtime, request.target, environment, request.base_dir, client
    )
