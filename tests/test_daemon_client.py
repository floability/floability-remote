import unittest
from unittest import mock

from floability_remote.application.errors import OperationError
from floability_remote.daemon.client import DaemonClient
from floability_remote.daemon.session import DaemonDescriptor


def run_payload(state="running"):
    return {
        "id": "run-1",
        "mode": "execute",
        "state": state,
        "target": "user@login.example.org",
        "repository": "https://github.com/example/demo.git",
        "source_type": "git",
        "local_path": "",
        "ref": "abc123",
        "batch_type": "slurm",
        "entrypoint": "main.py",
        "created_at": 1.0,
        "event_count": 1,
    }


class RecordingClient(DaemonClient):
    def __init__(self):
        super().__init__(
            DaemonDescriptor(pid=1, port=8000, token="private", started_at=1.0)
        )
        self.calls = []

    def request(self, method, path, payload=None, timeout=5):
        self.calls.append((method, path, payload, timeout))
        if path.endswith("/interactive/url"):
            return {
                "run_id": "run-1",
                "url": "http://127.0.0.1:49172/lab/?token=private",
            }
        if path.endswith("/interactive"):
            return {
                "run_id": "run-1",
                "state": "ready",
                "run_state": "running",
                "tunnel_ready": True,
                "local_port": 49172,
                "remote_port": 8888,
                "url_available": True,
                "message": "ready",
                "cancel_requested": False,
            }
        if path.endswith("/files/file-1/content"):
            return {
                "run_id": "run-1",
                "file_id": "file-1",
                "group": "workflow",
                "path": "workflow/result.csv",
                "size": 9,
                "content": "value,42\n",
            }
        if path.endswith("/files/download"):
            return {
                "run_id": "run-1",
                "destination": "/tmp/results",
                "file_count": 1,
                "total_bytes": 9,
                "files": [],
            }
        if path.endswith("/files"):
            return {
                "run_id": "run-1",
                "instance_found": True,
                "truncated": False,
                "files": [],
            }
        if path.endswith("/event-page?cursor=4&limit=12&include_logs=true"):
            return {
                "run": run_payload(),
                "events": [],
                "next_cursor": 4,
                "has_more": False,
                "terminal": False,
                "timed_out": False,
            }
        if "/wait?" in path:
            return {
                "run": run_payload(),
                "events": [],
                "next_cursor": 2,
                "has_more": False,
                "terminal": False,
                "timed_out": True,
            }
        return run_payload()


class DaemonClientPhase4Tests(unittest.TestCase):
    def test_plan_start_and_cursor_paths(self):
        client = RecordingClient()

        self.assertEqual(client.start_deployment("plan-1").id, "run-1")
        page = client.get_run_events("run-1", 4, 12, True)
        waited = client.wait_for_run("run-1", 2, 7, 20, False)

        self.assertEqual(page.next_cursor, 4)
        self.assertTrue(waited.timed_out)
        self.assertEqual(client.calls[0][:2], ("POST", "/plans/plan-1/start"))
        self.assertIn(
            "/runs/run-1/wait?cursor=2&timeout_seconds=7&limit=20&include_logs=false",
            client.calls[2][1],
        )
        self.assertEqual(client.calls[2][3], 12.0)

    def test_confirmation_posts_answer_then_refreshes_run(self):
        client = RecordingClient()

        response = client.answer_run_confirmation("run-1", "question-1", True)

        self.assertEqual(response.id, "run-1")
        self.assertEqual(
            client.calls[0][:3],
            (
                "POST",
                "/runs/run-1/confirmations/question-1",
                {"approved": True},
            ),
        )
        self.assertEqual(client.calls[1][:2], ("GET", "/runs/run-1"))

    def test_interactive_status_and_trusted_url_paths(self):
        client = RecordingClient()

        session = client.get_interactive_session("run-1")
        trusted = client.get_interactive_url("run-1")

        self.assertEqual(session.local_port, 49172)
        self.assertTrue(session.url_available)
        self.assertIn("token=private", trusted.url)
        self.assertEqual(client.calls[0][:2], ("GET", "/runs/run-1/interactive"))
        self.assertEqual(client.calls[1][:2], ("GET", "/runs/run-1/interactive/url"))

    def test_result_list_read_and_batch_download_paths(self):
        from floability_remote.application.contracts import RunFilesDownloadRequest

        client = RecordingClient()

        self.assertTrue(client.list_files("run-1").instance_found)
        self.assertIn("42", client.read_run_file("run-1", "file-1").content)
        response = client.download_run_files(
            "run-1",
            RunFilesDownloadRequest(
                destination="/tmp/results", file_ids=["file-1"]
            ),
        )

        self.assertEqual(response.file_count, 1)
        self.assertEqual(client.calls[0][:2], ("GET", "/runs/run-1/files"))
        self.assertEqual(
            client.calls[1][:2], ("GET", "/runs/run-1/files/file-1/content")
        )
        self.assertEqual(client.calls[2][0:2], ("POST", "/runs/run-1/files/download"))
        self.assertEqual(client.calls[2][2]["file_ids"], ["file-1"])
        self.assertEqual(client.calls[2][3], 600)

    def test_missing_preview_route_is_reported_as_a_stale_daemon(self):
        client = RecordingClient()
        with mock.patch.object(
            client,
            "request",
            side_effect=OperationError("not_found", "Not Found", status=404),
        ):
            with self.assertRaisesRegex(OperationError, "updated daemon") as raised:
                client.read_run_file("run-1", "file-1")

        self.assertEqual(raised.exception.code, "daemon_update_required")


if __name__ == "__main__":
    unittest.main()
