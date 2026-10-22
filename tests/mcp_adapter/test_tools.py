import unittest

from floability_remote.application.contracts import (
    ClusterCheckResponse,
    ConnectionResponse,
)
from floability_remote.application.errors import OperationError
from floability_remote.mcp.tools import MCPTools


class FakeClient:
    def __init__(self):
        self.connection = ConnectionResponse(
            state="connected",
            target="user@login.example.edu",
            remote_user="user",
            remote_host="login.example.edu",
        )
        self.connect_request = None
        self.cluster_request = None

    def meta(self):
        return {"version": "0.1.0", "api_version": "v1"}

    def get_connection(self):
        return self.connection

    def connect(self, request):
        self.connect_request = request
        self.connection = ConnectionResponse(
            state="connected",
            target=request.target,
            identity_file=request.identity_file,
            remote_user="user",
            remote_host="login.example.edu",
        )
        return self.connection

    def disconnect(self):
        self.connection = ConnectionResponse(state="disconnected")
        return self.connection

    def check_cluster(self, request):
        self.cluster_request = request
        return ClusterCheckResponse(
            ready=True,
            remote_user="user",
            remote_host="login.example.edu",
            os_name="Linux",
            architecture="x86_64",
            git_available=True,
            setsid_available=True,
            conda="/opt/conda/bin/conda",
            env_name=request.env_name,
            env_prefix=f"/opt/conda/envs/{request.env_name}",
            floability_version="0.3.1",
            requested_base_dir=request.base_dir or "~/floability-base-dir",
            resolved_base_dir="/home/user/floability-base-dir",
            storage_path="/home/user",
            total_bytes=1000,
            free_bytes=750,
            quota_status="not-reported",
            quota_summary="",
            available_batch_types=["local", "slurm"],
            issues=[],
        )

    def current_run(self):
        raise OperationError("run_not_found", "No run has been started.", status=404)


class MCPToolsTests(unittest.TestCase):
    def test_status_combines_daemon_and_connection_without_a_run(self):
        calls = []

        def factory(client, session_id):
            calls.append((client, session_id))
            return FakeClient()

        tools = MCPTools(client_factory=factory, session_id="mcp-session")
        status = tools.get_status()

        self.assertTrue(status.daemon_running)
        self.assertEqual(status.connection_state, "connected")
        self.assertEqual(status.connection_target, "user@login.example.edu")
        self.assertIsNone(status.current_run)
        self.assertEqual(calls, [("mcp", "mcp-session")])

    def test_status_explains_how_to_start_a_missing_daemon(self):
        def missing(client, session_id):
            raise OperationError(
                "daemon_not_running", "Floability Remote daemon is not running."
            )

        status = MCPTools(client_factory=missing).get_status()

        self.assertFalse(status.daemon_running)
        self.assertIn("daemon start --no-browser", status.message)

    def test_get_connection_preserves_expected_daemon_errors(self):
        def missing(client, session_id):
            raise OperationError(
                "daemon_not_running", "Floability Remote daemon is not running."
            )

        with self.assertRaisesRegex(OperationError, "daemon is not running"):
            MCPTools(client_factory=missing).get_connection()

    def test_connect_disconnect_and_check_reuse_the_same_daemon_connection(self):
        client = FakeClient()
        client.connection = ConnectionResponse(state="disconnected")
        tools = MCPTools(client_factory=lambda kind, session: client)

        connected = tools.connect_remote(
            "user@login.example.edu", "/home/me/.ssh/id_ed25519"
        )
        self.assertEqual(connected.state, "connected")
        self.assertEqual(client.connect_request.target, "user@login.example.edu")
        self.assertEqual(
            client.connect_request.identity_file, "/home/me/.ssh/id_ed25519"
        )

        report = tools.check_remote(env_name="floability-test", base_dir="~/runs")
        self.assertTrue(report.ready)
        self.assertEqual(report.available_batch_types, ["local", "slurm"])
        self.assertEqual(client.cluster_request.target, "user@login.example.edu")
        self.assertEqual(client.cluster_request.env_name, "floability-test")

        self.assertEqual(tools.disconnect_remote().state, "disconnected")

    def test_check_remote_requires_a_connected_host(self):
        client = FakeClient()
        client.connection = ConnectionResponse(state="disconnected")
        tools = MCPTools(client_factory=lambda kind, session: client)

        with self.assertRaisesRegex(OperationError, "Connect to a remote host"):
            tools.check_remote()


if __name__ == "__main__":
    unittest.main()
