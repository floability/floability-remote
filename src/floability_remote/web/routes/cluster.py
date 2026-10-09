"""Read-only readiness information for the connected login node."""

from fastapi import APIRouter, Depends

from ...cluster import ClusterService, validate_cluster_settings
from ...config import EnvironmentConfig
from ...connection import ConnectionConflict
from ...errors import RemoteRunError
from ..errors import ApiError
from ..schemas import ClusterCheckRequest, ClusterCheckResponse
from ..services import Services, get_services


router = APIRouter(prefix="/cluster", tags=["cluster"])


@router.post("/check", response_model=ClusterCheckResponse)
def check_cluster(
    request: ClusterCheckRequest,
    services: Services = Depends(get_services),
) -> ClusterCheckResponse:
    environment = EnvironmentConfig(
        env_name=request.env_name,
        floability_version=request.floability_version,
        conda_executable=request.conda_executable,
    )
    try:
        validate_cluster_settings(environment, request.base_dir)
        session = services.connections.session_for(request.target)
        report = ClusterService(session).check(environment, request.base_dir)
    except ConnectionConflict as error:
        raise ApiError(409, "connection_required", str(error)) from error
    except RemoteRunError as error:
        raise ApiError(422, "cluster_check_failed", str(error)) from error

    return ClusterCheckResponse(
        ready=report.ready,
        remote_user=report.remote_user,
        remote_host=report.remote_host,
        os_name=report.probe.os_name,
        architecture=report.probe.architecture,
        git_available=report.probe.git_available,
        setsid_available=report.probe.setsid_available,
        conda=report.probe.conda,
        env_name=report.env_name,
        env_prefix=report.probe.env_prefix,
        floability_version=report.probe.floability_version,
        requested_base_dir=report.requested_base_dir,
        resolved_base_dir=report.resolved_base_dir,
        storage_path=report.storage_path,
        total_bytes=report.total_bytes,
        free_bytes=report.free_bytes,
        quota_status=report.quota_status,
        quota_summary=report.quota_summary,
        issues=list(report.issues),
    )
