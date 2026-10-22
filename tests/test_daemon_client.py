import unittest

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


if __name__ == "__main__":
    unittest.main()
