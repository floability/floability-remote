"""Interface-neutral implementations behind the MCP tools."""

import secrets
import time
from typing import Callable, List, Optional

from .. import __version__
from ..application.contracts import (
    ClusterCheckRequest,
    ClusterCheckResponse,
    BackpackFileListResponse,
    BackpackFileResponse,
    BackpackSourceCheckResponse,
    BackpackSourceRequest,
    BackpackSourceResponse,
    ConnectionResponse,
    ConnectRequest,
    DeploymentPlanRequest,
    DeploymentPlanResponse,
    EnvironmentModel,
    FloabilityOptionModel,
    RunResponse,
)
from ..application.errors import OperationError
from ..daemon.client import DaemonClient
from ..domain.config import DEFAULT_ENV_NAME
from ..domain.config import DEFAULT_JUPYTER_PORT, DEFAULT_REMOTE_ROOT
from .schemas import RunStatusSummary, StatusResult


ClientFactory = Callable[[str, str], DaemonClient]


def _discover_client(client: str, session_id: str) -> DaemonClient:
    return DaemonClient.discover(client=client, session_id=session_id)


class MCPTools:
    """Read-only daemon facade shared by the MCP registrations and tests."""

    def __init__(
        self,
        client_factory: Optional[ClientFactory] = None,
        session_id: str = "",
    ):
        self._client_factory = client_factory or _discover_client
        self.session_id = session_id or secrets.token_hex(12)

    def _client(self) -> DaemonClient:
        return self._client_factory("mcp", self.session_id)

    def get_status(self) -> StatusResult:
        """Return daemon, connection, and current-run status without changing state."""
        try:
            client = self._client()
        except OperationError as error:
            if error.code == "daemon_not_running":
                return StatusResult(
                    client_version=__version__,
                    daemon_running=False,
                    message=(
                        "The local daemon is not running. Start it with "
                        "`floability-remote daemon start --no-browser`."
                    ),
                )
            raise

        metadata = client.meta()
        connection = client.get_connection()
        current_run = self._current_run(client)
        return StatusResult(
            client_version=__version__,
            daemon_running=True,
            daemon_version=str(metadata.get("version", "")) or None,
            api_version=str(metadata.get("api_version", "")) or None,
            connection_state=connection.state,
            connection_target=connection.target,
            current_run=self._run_summary(current_run) if current_run else None,
            message="Floability Remote daemon is ready.",
        )

    def get_connection(self) -> ConnectionResponse:
        """Return the daemon-owned SSH connection state and any pending prompt metadata."""
        return self._client().get_connection()

    def connect_remote(
        self, target: str, identity_file: str = ""
    ) -> ConnectionResponse:
        """Start a daemon-owned SSH connection without accepting SSH secrets.

        Passwords, MFA responses, key passphrases, and host-key answers are
        deliberately absent from this interface. A terminal control plane may
        answer a pending AskPass prompt directly through the daemon.
        """
        client = self._client()
        connection = client.connect(
            ConnectRequest(target=target, identity_file=identity_file or None)
        )
        # Give key- and agent-authenticated connections time to finish. For an
        # interactive login, return as soon as AskPass reports a pending prompt.
        deadline = time.monotonic() + 5
        while connection.state == "connecting" and connection.prompt is None:
            if time.monotonic() >= deadline:
                break
            time.sleep(0.05)
            connection = client.get_connection()
        return connection

    def disconnect_remote(self) -> ConnectionResponse:
        """Close the daemon-owned SSH connection."""
        return self._client().disconnect()

    def check_remote(
        self,
        env_name: str = DEFAULT_ENV_NAME,
        floability_version: str = "",
        conda_executable: str = "",
        base_dir: str = "",
    ) -> ClusterCheckResponse:
        """Inspect identity, platform, Floability, storage, and batch systems."""
        client = self._client()
        connection = client.get_connection()
        if connection.state != "connected" or not connection.target:
            raise OperationError(
                "connection_required",
                "Connect to a remote host before checking its readiness.",
                status=409,
            )
        return client.check_cluster(
            ClusterCheckRequest(
                target=connection.target,
                env_name=env_name,
                floability_version=floability_version,
                conda_executable=conda_executable,
                base_dir=base_dir,
            )
        )

    def inspect_backpack(
        self,
        source_type: str = "git",
        repository: str = "",
        ref: str = "",
        local_path: str = "",
    ) -> BackpackSourceResponse:
        """Inspect and fingerprint a Git, local-folder, or ZIP backpack."""
        return self._client().inspect_backpack_source(
            BackpackSourceRequest(
                source_type=source_type,
                repository=repository,
                ref=ref,
                local_path=local_path,
            )
        )

    def check_backpack_source(self, source_id: str) -> BackpackSourceCheckResponse:
        """Report whether an inspected backpack source is still unchanged."""
        return self._client().check_backpack_source(source_id)

    def list_backpack_files(self, source_id: str) -> BackpackFileListResponse:
        """List regular files included in an inspected backpack."""
        return self._client().list_backpack_files(source_id)

    def read_backpack_file(self, source_id: str, path: str) -> BackpackFileResponse:
        """Read one small UTF-8 file from an inspected backpack."""
        return self._client().read_backpack_file(source_id, path)

    def validate_deployment(
        self,
        source_id: str,
        mode: str,
        batch_type: str,
        entrypoint: str = "",
        env_name: str = DEFAULT_ENV_NAME,
        floability_version: str = "",
        conda_executable: str = "",
        reinstall_miniforge: bool = False,
        remote_root: str = DEFAULT_REMOTE_ROOT,
        base_dir: str = "",
        data_cache_dir: str = "",
        jupyter_port: int = DEFAULT_JUPYTER_PORT,
        local_port: Optional[int] = None,
        floability_options: Optional[List[FloabilityOptionModel]] = None,
    ) -> DeploymentPlanResponse:
        """Validate a deployment and create a plan ID without starting it."""
        return self._client().validate_deployment_plan(
            DeploymentPlanRequest(
                source_id=source_id,
                mode=mode,
                batch_type=batch_type,
                entrypoint=entrypoint,
                environment=EnvironmentModel(
                    env_name=env_name,
                    floability_version=floability_version,
                    conda_executable=conda_executable,
                    reinstall_miniforge=reinstall_miniforge,
                ),
                remote_root=remote_root,
                base_dir=base_dir,
                data_cache_dir=data_cache_dir,
                jupyter_port=jupyter_port,
                local_port=local_port,
                floability_options=floability_options or [],
            )
        )

    @staticmethod
    def _current_run(client: DaemonClient) -> Optional[RunResponse]:
        try:
            return client.current_run()
        except OperationError as error:
            if error.code == "run_not_found":
                return None
            raise

    @staticmethod
    def _run_summary(run: RunResponse) -> RunStatusSummary:
        return RunStatusSummary(
            id=run.id,
            mode=run.mode,
            state=run.state,
            target=run.target,
            batch_type=run.batch_type,
            entrypoint=run.entrypoint,
            created_at=run.created_at,
            finished_at=run.finished_at,
            message=run.message,
        )
