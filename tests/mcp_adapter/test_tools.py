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
    IndexedEventModel,
    InteractiveSessionResponse,
    FileListResponse,
    RemoteFileModel,
    RunFileContentResponse,
    RunFilesDownloadResponse,
    RunEventPageResponse,
    RunResponse,
    TaskVineStatusResponse,
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

    def test_phase4_tools_preserve_run_id_cursor_and_confirmation(self):
        client = mock.Mock()
        run = RunResponse(
            id="run-1",
            mode="execute",
            state="running",
            target="user@login.example.edu",
            repository="https://github.com/example/demo.git",
            ref="abc123",
            batch_type="slurm",
            entrypoint="main.py",
            created_at=1.0,
            event_count=1,
            jupyter_url="http://127.0.0.1:8888/?token=private-token",
        )
        page = RunEventPageResponse(
            run=run,
            events=[
                IndexedEventModel(
                    cursor=0,
                    kind="ready",
                    message="Open http://127.0.0.1:8888/?token=private-token",
                    data={"url": "http://127.0.0.1:8888/?token=private-token"},
                    timestamp=1.0,
                )
            ],
            next_cursor=1,
            has_more=False,
            terminal=False,
        )
        client.start_deployment.return_value = run
        client.get_run.return_value = run
        client.wait_for_run.return_value = page
        client.get_run_events.return_value = page
        client.answer_run_confirmation.return_value = run
        client.cancel_run.return_value = run
        tools = MCPTools(client_factory=lambda kind, session: client)

        self.assertIsNone(tools.start_deployment("plan-1").jupyter_url)
        self.assertIsNone(tools.get_run("run-1").jupyter_url)
        self.assertEqual(
            tools.wait_for_run(
                "run-1", cursor=-4, timeout_seconds=60, limit=500
            ).next_cursor,
            1,
        )
        self.assertEqual(
            tools.get_run_events("run-1", cursor=5).events[0].message,
            "Interactive session is ready in the browser.",
        )
        self.assertNotIn("url", tools.get_run_events("run-1", cursor=5).events[0].data)
        tools.answer_confirmation("run-1", "confirm-1", True)
        tools.cancel_run("run-1")

        client.start_deployment.assert_called_once_with("plan-1")
        client.wait_for_run.assert_called_once_with("run-1", 0, 30, 200, False)
        self.assertEqual(client.get_run_events.call_count, 2)
        client.get_run_events.assert_called_with("run-1", 5, 50, False)
        client.answer_run_confirmation.assert_called_once_with(
            "run-1", "confirm-1", True
        )
        client.cancel_run.assert_called_once_with("run-1")

    def test_phase5_tools_return_model_safe_session_and_taskvine_state(self):
        client = mock.Mock()
        client.get_interactive_session.return_value = InteractiveSessionResponse(
            run_id="run-1",
            state="ready",
            run_state="running",
            tunnel_ready=True,
            local_port=49172,
            remote_port=8888,
            url_available=True,
            message="Jupyter is ready through the daemon-owned SSH tunnel.",
        )
        client.taskvine_status.return_value = TaskVineStatusResponse(
            run_id="run-1",
            available=True,
            uses_taskvine=True,
            manager_state="running",
            workers_connected=2,
            checked_at=1.0,
        )
        tools = MCPTools(client_factory=lambda kind, session: client)

        interactive = tools.get_interactive_session("run-1")
        taskvine = tools.get_taskvine_status("run-1")

        self.assertEqual(interactive.local_port, 49172)
        self.assertTrue(interactive.url_available)
        self.assertFalse(hasattr(interactive, "url"))
        self.assertEqual(taskvine.workers_connected, 2)
        client.get_interactive_session.assert_called_once_with("run-1")
        client.taskvine_status.assert_called_once_with("run-1")

    def test_phase6_tools_list_read_and_download_selected_results(self):
        client = mock.Mock()
        file = RemoteFileModel(
            id="file-1",
            group="workflow",
            path="workflow/result.csv",
            size=9,
            downloadable=True,
        )
        client.list_files.return_value = FileListResponse(
            run_id="run-1", instance_found=True, truncated=False, files=[file]
        )
        client.read_run_file.return_value = RunFileContentResponse(
            run_id="run-1",
            file_id="file-1",
            group="workflow",
            path="workflow/result.csv",
            size=9,
            content="value,42\n",
        )
        client.download_run_files.return_value = RunFilesDownloadResponse(
            run_id="run-1",
            destination="/tmp/results",
            file_count=1,
            total_bytes=9,
            files=[],
        )
        tools = MCPTools(client_factory=lambda kind, session: client)

        self.assertEqual(tools.list_run_files("run-1").files[0].id, "file-1")
        self.assertIn("42", tools.read_run_file("run-1", "file-1").content)
        response = tools.download_run_files(
            "run-1", "/tmp/results", file_ids=["file-1"]
        )
        self.assertEqual(response.file_count, 1)

        client.list_files.assert_called_once_with("run-1")
        client.read_run_file.assert_called_once_with("run-1", "file-1")
        request = client.download_run_files.call_args.args[1]
        self.assertEqual(request.file_ids, ["file-1"])
        self.assertFalse(request.all_files)


if __name__ == "__main__":
    unittest.main()
