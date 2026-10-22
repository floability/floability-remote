"""Official MCP SDK registrations and stdio server entry point."""

from typing import Annotated, List, Literal, Optional

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from ..application.contracts import (
    BackpackFileListResponse,
    BackpackFileResponse,
    BackpackSourceCheckResponse,
    BackpackSourceResponse,
    ClusterCheckResponse,
    ConnectionResponse,
    DeploymentPlanResponse,
    FloabilityOptionModel,
    InteractiveSessionResponse,
    RunEventPageResponse,
    RunResponse,
    TaskVineStatusResponse,
)
from ..domain.config import (
    DEFAULT_ENV_NAME,
    DEFAULT_JUPYTER_PORT,
    DEFAULT_REMOTE_ROOT,
)
from .errors import tool_errors
from .instructions import SERVER_INSTRUCTIONS
from .schemas import StatusResult
from .tools import MCPTools


READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)

STATE_CHANGE = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=False,
    open_world_hint=True,
)

REMOTE_READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=True,
)


def create_server(tools: Optional[MCPTools] = None) -> MCPServer:
    """Build one stateless stdio adapter over a daemon-backed tool facade."""
    facade = tools or MCPTools()
    server = MCPServer("floability-remote", instructions=SERVER_INSTRUCTIONS)

    @server.tool(annotations=READ_ONLY)
    @tool_errors
    def get_status() -> StatusResult:
        """Get local daemon readiness, connection state, and current run summary."""
        return facade.get_status()

    @server.tool(annotations=READ_ONLY)
    @tool_errors
    def get_connection() -> ConnectionResponse:
        """Get details of the SSH connection owned by the local daemon."""
        return facade.get_connection()

    @server.tool(annotations=STATE_CHANGE)
    @tool_errors
    def connect_remote(target: str, identity_file: str = "") -> ConnectionResponse:
        """Start SSH to user@host. Never pass passwords, MFA, or passphrases here."""
        return facade.connect_remote(target, identity_file)

    @server.tool(annotations=STATE_CHANGE)
    @tool_errors
    def disconnect_remote() -> ConnectionResponse:
        """Close the current SSH connection; an active run prevents disconnecting."""
        return facade.disconnect_remote()

    @server.tool(annotations=REMOTE_READ_ONLY)
    @tool_errors
    def check_remote(
        env_name: str = DEFAULT_ENV_NAME,
        floability_version: str = "",
        conda_executable: str = "",
        base_dir: str = "",
    ) -> ClusterCheckResponse:
        """Check the connected host's OS, Floability environment, storage, and schedulers."""
        return facade.check_remote(
            env_name, floability_version, conda_executable, base_dir
        )

    @server.tool(annotations=REMOTE_READ_ONLY)
    @tool_errors
    def inspect_backpack(
        source_type: Literal["git", "folder", "zip"] = "git",
        repository: str = "",
        ref: str = "",
        local_path: str = "",
    ) -> BackpackSourceResponse:
        """Inspect a Git repository, local folder, or ZIP and discover entrypoints."""
        return facade.inspect_backpack(source_type, repository, ref, local_path)

    @server.tool(annotations=READ_ONLY)
    @tool_errors
    def check_backpack_source(source_id: str) -> BackpackSourceCheckResponse:
        """Check whether an inspected backpack is still available and unchanged."""
        return facade.check_backpack_source(source_id)

    @server.tool(annotations=READ_ONLY)
    @tool_errors
    def list_backpack_files(source_id: str) -> BackpackFileListResponse:
        """List files in an inspected backpack; generated artifacts are excluded."""
        return facade.list_backpack_files(source_id)

    @server.tool(annotations=READ_ONLY)
    @tool_errors
    def read_backpack_file(source_id: str, path: str) -> BackpackFileResponse:
        """Read one UTF-8 backpack file up to 128 KB; paths cannot escape the source."""
        return facade.read_backpack_file(source_id, path)

    @server.tool(annotations=REMOTE_READ_ONLY)
    @tool_errors
    def validate_deployment(
        source_id: str,
        mode: Literal["run", "execute"],
        batch_type: Literal["local", "slurm", "condor", "uge"],
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
        """Validate settings and return a plan ID; this never starts a deployment."""
        return facade.validate_deployment(
            source_id,
            mode,
            batch_type,
            entrypoint,
            env_name,
            floability_version,
            conda_executable,
            reinstall_miniforge,
            remote_root,
            base_dir,
            data_cache_dir,
            jupyter_port,
            local_port,
            floability_options,
        )

    @server.tool(annotations=STATE_CHANGE)
    @tool_errors
    def start_deployment(plan_id: str) -> RunResponse:
        """Start an execute or interactive deployment from a validated plan ID."""
        return facade.start_deployment(plan_id)

    @server.tool(annotations=READ_ONLY)
    @tool_errors
    def get_run(run_id: str) -> RunResponse:
        """Get state, pending confirmation, workspace, and log for one run."""
        return facade.get_run(run_id)

    @server.tool(annotations=READ_ONLY)
    @tool_errors
    def wait_for_run(
        run_id: str,
        cursor: Annotated[int, Field(ge=0)] = 0,
        timeout_seconds: Annotated[float, Field(ge=0, le=30)] = 10,
        limit: Annotated[int, Field(ge=1, le=200)] = 50,
        include_logs: bool = False,
    ) -> RunEventPageResponse:
        """Wait at most 30 seconds for redacted progress or completion."""
        return facade.wait_for_run(run_id, cursor, timeout_seconds, limit, include_logs)

    @server.tool(annotations=READ_ONLY)
    @tool_errors
    def get_run_events(
        run_id: str,
        cursor: Annotated[int, Field(ge=0)] = 0,
        limit: Annotated[int, Field(ge=1, le=200)] = 50,
        include_logs: bool = False,
    ) -> RunEventPageResponse:
        """Read a cursor-based page of retained, redacted run events."""
        return facade.get_run_events(run_id, cursor, limit, include_logs)

    @server.tool(annotations=STATE_CHANGE)
    @tool_errors
    def answer_confirmation(
        run_id: str, confirmation_id: str, approved: bool
    ) -> RunResponse:
        """Approve or decline a pending environment-installation question."""
        return facade.answer_confirmation(run_id, confirmation_id, approved)

    @server.tool(annotations=STATE_CHANGE)
    @tool_errors
    def cancel_run(run_id: str) -> RunResponse:
        """Cancel an active deployment through Floability's cleanup path."""
        return facade.cancel_run(run_id)

    @server.tool(annotations=READ_ONLY)
    @tool_errors
    def get_interactive_session(run_id: str) -> InteractiveSessionResponse:
        """Get Jupyter and tunnel readiness without exposing the Jupyter token."""
        return facade.get_interactive_session(run_id)

    @server.tool(annotations=REMOTE_READ_ONLY)
    @tool_errors
    def get_taskvine_status(run_id: str) -> TaskVineStatusResponse:
        """Get TaskVine manager, worker, task, and factory status for a run."""
        return facade.get_taskvine_status(run_id)

    return server


def run() -> None:
    """Serve MCP JSON-RPC over stdio; diagnostics belong on stderr."""
    create_server().run()


if __name__ == "__main__":
    run()
