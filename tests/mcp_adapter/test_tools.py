import unittest
from unittest import mock

from floability_remote.application.contracts import (
    BackpackFileListResponse,
    BackpackFileModel,
    BackpackFileResponse,
    BackpackInspectionResponse,
    BackpackSourceCheckResponse,
    BackpackSourceResponse,
    ClusterCheckResponse,
    ConnectionResponse,
    DeploymentPlanResponse,
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

    def test_phase3_tools_share_source_and_plan_identifiers(self):
        client = mock.Mock()
        client.inspect_backpack_source.return_value = BackpackSourceResponse(
            source_id="source-1",
            source_type="git",
            location="https://github.com/example/demo.git",
            commit="abc123",
            resolved_ref="abc123",
            inspection=BackpackInspectionResponse(
                path="/tmp/demo",
                name="demo",
                source_type="folder",
                file_count=2,
                total_bytes=20,
                workflow_found=True,
                valid=True,
                problems=[],
                workflow_files=["main.py"],
            ),
            entrypoints=["main.py"],
            created_at=1.0,
        )
        client.check_backpack_source.return_value = BackpackSourceCheckResponse(
            source_id="source-1", fresh=True, message="unchanged"
        )
        client.list_backpack_files.return_value = BackpackFileListResponse(
            source_id="source-1",
            files=[BackpackFileModel(path="workflow/main.py", size=10)],
        )
        client.read_backpack_file.return_value = BackpackFileResponse(
            source_id="source-1", path="workflow/main.py", content="print('ok')"
        )
        client.validate_deployment_plan.return_value = DeploymentPlanResponse(
            valid=True,
            plan_id="plan-1",
            source_id="source-1",
            target="user@login.example.edu",
            mode="execute",
            batch_type="local",
            entrypoint="main.py",
            command="floability-remote execute ...",
            created_at=2.0,
        )
        tools = MCPTools(client_factory=lambda kind, session: client)

        source = tools.inspect_backpack(
            repository="https://github.com/example/demo.git"
        )
        self.assertEqual(source.source_id, "source-1")
        self.assertTrue(tools.check_backpack_source(source.source_id).fresh)
        self.assertEqual(
            tools.list_backpack_files(source.source_id).files[0].path,
            "workflow/main.py",
        )
        self.assertIn(
            "print",
            tools.read_backpack_file(source.source_id, "workflow/main.py").content,
        )
        plan = tools.validate_deployment(source.source_id, "execute", "local")
        self.assertEqual(plan.plan_id, "plan-1")
        request = client.validate_deployment_plan.call_args.args[0]
        self.assertEqual(request.source_id, "source-1")
        self.assertEqual(request.environment.env_name, "floability-remote-managed")


if __name__ == "__main__":
    unittest.main()
