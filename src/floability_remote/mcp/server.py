"""Official MCP SDK registrations and stdio server entry point."""

from typing import Optional

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from ..application.contracts import ClusterCheckResponse, ConnectionResponse
from ..domain.config import DEFAULT_ENV_NAME
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

    return server


def run() -> None:
    """Serve MCP JSON-RPC over stdio; diagnostics belong on stderr."""
    create_server().run()


if __name__ == "__main__":
    run()
