import importlib.util
import unittest

from floability_remote.application.contracts import ConnectionResponse
from floability_remote.application.errors import OperationError
from floability_remote.mcp.tools import MCPTools


MCP_AVAILABLE = importlib.util.find_spec("mcp") is not None


class FakeClient:
    def __init__(self):
        self.connection = ConnectionResponse(state="disconnected")

    def meta(self):
        return {"version": "0.1.0", "api_version": "v1"}

    def get_connection(self):
        return self.connection

    def connect(self, request):
        self.connection = ConnectionResponse(
            state="connected",
            target=request.target,
            remote_user="user",
            remote_host="login.example.edu",
        )
        return self.connection

    def current_run(self):
        raise OperationError("run_not_found", "No run has been started.", status=404)


@unittest.skipUnless(MCP_AVAILABLE, "install the mcp extra to run MCP protocol tests")
class MCPServerTests(unittest.IsolatedAsyncioTestCase):
    async def test_tools_are_structured_and_read_only(self):
        from mcp import Client

        from floability_remote.mcp.server import create_server

        facade = MCPTools(client_factory=lambda client, session: FakeClient())
        async with Client(create_server(facade)) as client:
            catalog = await client.list_tools()
            tools = {tool.name: tool for tool in catalog.tools}

            self.assertEqual(
                set(tools),
                {
                    "get_status",
                    "get_connection",
                    "connect_remote",
                    "disconnect_remote",
                    "check_remote",
                },
            )
            self.assertTrue(tools["get_status"].annotations.read_only_hint)
            self.assertFalse(tools["get_status"].annotations.destructive_hint)
            self.assertFalse(tools["connect_remote"].annotations.read_only_hint)
            self.assertTrue(tools["check_remote"].annotations.read_only_hint)
            self.assertEqual(
                set(tools["connect_remote"].input_schema["properties"]),
                {"target", "identity_file"},
            )

            result = await client.call_tool("get_status", {})
            self.assertFalse(result.is_error)
            self.assertTrue(result.structured_content["daemon_running"])
            self.assertEqual(
                result.structured_content["connection_state"], "disconnected"
            )

            connected = await client.call_tool(
                "connect_remote", {"target": "user@login.example.edu"}
            )
            self.assertFalse(connected.is_error)
            self.assertEqual(connected.structured_content["state"], "connected")

    async def test_expected_operation_error_is_visible_to_the_model(self):
        from mcp import Client

        from floability_remote.mcp.server import create_server

        def missing(client, session):
            raise OperationError(
                "daemon_not_running", "Floability Remote daemon is not running."
            )

        async with Client(create_server(MCPTools(client_factory=missing))) as client:
            result = await client.call_tool("get_connection", {})

        self.assertTrue(result.is_error)
        self.assertIn("daemon_not_running", result.content[0].text)
        self.assertIn("daemon start --no-browser", result.content[0].text)


if __name__ == "__main__":
    unittest.main()
