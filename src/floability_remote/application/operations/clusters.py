"""Remote-host readiness operations."""

from ...domain.config import EnvironmentConfig
from ...domain.errors import RemoteRunError
from ...remote.connection import ConnectionConflict
from ...services.cluster import ClusterService, validate_cluster_settings
from ..errors import OperationError
from ..contracts import ClusterCheckResponse
from ..runtime import ApplicationRuntime
from . import ClientContext


def response(report) -> ClusterCheckResponse:
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
        available_batch_types=list(report.available_batch_types),
        issues=list(report.issues),
    )


def check_with_session(session, environment: EnvironmentConfig, base_dir: str):
    validate_cluster_settings(environment, base_dir)
    return response(ClusterService(session).check(environment, base_dir))


def check(
    runtime: ApplicationRuntime,
    target: str,
    environment: EnvironmentConfig,
    base_dir: str,
    client: ClientContext,
) -> ClusterCheckResponse:
    with runtime.audit.operation(
        "cluster.check",
        client=client.kind,
        session_id=client.session_id,
        target=target,
        inputs={"env_name": environment.env_name, "base_dir": base_dir},
    ):
        try:
            session = runtime.connections.session_for(target)
            return check_with_session(session, environment, base_dir)
        except ConnectionConflict as error:
            raise OperationError("connection_required", str(error), status=409) from error
        except RemoteRunError as error:
            raise OperationError("cluster_check_failed", str(error), status=422) from error
