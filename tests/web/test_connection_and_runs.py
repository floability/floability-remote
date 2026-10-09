import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from fake_ssh import PASSWORD, fake_ssh
from floability_remote.events import EventKind
from floability_remote.interaction import INSTALL_MINIFORGE, ConfirmationRequest
from test_runs import ScriptedWorkflow

from .support import make_client, requires_web, run_request

TARGET = "login.example.org"


def parse_sse(text):
    """Return [(event name, id, data)] from a complete SSE response body."""
    messages = []
    for block in text.split("\n\n"):
        fields = {}
        for line in block.splitlines():
            if line.startswith(":") or ":" not in line:
                continue
            key, _, value = line.partition(":")
            fields[key] = value.strip()
        if "event" in fields:
            messages.append(
                (fields["event"], fields.get("id"), json.loads(fields["data"]))
            )
    return messages


@requires_web
class ConnectionAndRunApiTests(unittest.TestCase):
    def setUp(self):
        from floability_remote.connection import ConnectionManager
        from floability_remote.runs import RunManager
        from floability_remote.web.services import Services

        connections = ConnectionManager()
        self.services = Services(
            connections=connections,
            runs=RunManager(connections, workflow_factory=ScriptedWorkflow),
        )
        self.client = make_client(services=self.services)
        self.ssh = fake_ssh()
        self.ssh.__enter__()
        self.addCleanup(self.ssh.__exit__, None, None, None)
        self.addCleanup(self.services.close)

    def poll(self, path, condition, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            body = self.client.get(path).json()
            if condition(body):
                return body
            time.sleep(0.05)
        raise AssertionError(f"{path} never satisfied the condition: {body}")

    def connect(self):
        response = self.client.post("/api/v1/connection", json={"target": TARGET})
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["state"], "connecting")
        prompt = self.poll("/api/v1/connection", lambda body: body["prompt"])["prompt"]
        self.assertEqual(prompt["kind"], "secret")
        answered = self.client.post(
            f"/api/v1/connection/prompts/{prompt['id']}", json={"answer": PASSWORD}
        )
        self.assertEqual(answered.status_code, 204)
        return self.poll(
            "/api/v1/connection", lambda body: body["state"] != "connecting"
        )

    def start(self, script, **overrides):
        ScriptedWorkflow.script = script
        response = self.client.post(
            "/api/v1/runs", json=run_request(connection={"target": TARGET}, **overrides)
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    # Connection ------------------------------------------------------------

    def test_connect_answer_prompt_and_disconnect(self):
        connected = self.connect()
        self.assertEqual(connected["state"], "connected", connected["error"])
        self.assertTrue(connected["remote_user"])
        self.assertIsNone(connected["prompt"])

        again = self.client.post("/api/v1/connection", json={"target": TARGET})
        self.assertEqual(again.status_code, 409)

        response = self.client.delete("/api/v1/connection")
        self.assertEqual(response.json()["state"], "disconnected")

    def test_invalid_target_is_rejected_with_field_issue(self):
        response = self.client.post(
            "/api/v1/connection", json={"target": "-oProxyCommand=x"}
        )
        self.assertEqual(response.status_code, 422)
        error = response.json()["error"]
        self.assertEqual(error["code"], "invalid_config")
        self.assertEqual(error["issues"][0]["field"], "connection.target")

    def test_stale_prompt_answer(self):
        response = self.client.post(
            "/api/v1/connection/prompts/missing", json={"answer": "x"}
        )
        self.assertEqual(response.status_code, 404)

    def test_cancelled_prompt_fails_sign_in(self):
        self.client.post("/api/v1/connection", json={"target": TARGET})
        prompt = self.poll("/api/v1/connection", lambda body: body["prompt"])["prompt"]
        self.client.post(
            f"/api/v1/connection/prompts/{prompt['id']}", json={"cancel": True}
        )
        failed = self.poll("/api/v1/connection", lambda body: body["state"] == "failed")
        self.assertIn("Permission denied", failed["error"])

    # Runs ------------------------------------------------------------------

    def test_run_requires_connection(self):
        response = self.client.post(
            "/api/v1/runs", json=run_request(connection={"target": TARGET})
        )
        self.assertEqual(response.status_code, 409)
        self.assertIn("Connect", response.json()["error"]["message"])

    def test_interactive_session_exposes_link_until_stopped(self):
        self.connect()
        url = "http://127.0.0.1:49999/lab/?token=abc"

        def script(workflow):
            workflow.redactor.add("abc")
            workflow.emit(EventKind.LOG, "[jupyter] ... token abc.\n")
            workflow.emit(
                EventKind.READY, "Jupyter is ready.", url=url, local_port=49999
            )
            workflow.cancel_token.wait(10)
            workflow.emit(EventKind.CANCELLED, "The interactive session was stopped.")

        run = self.start(script, mode="run")
        self.assertEqual(run["mode"], "run")
        ready = self.poll(f"/api/v1/runs/{run['id']}", lambda body: body["jupyter_url"])
        self.assertEqual(ready["jupyter_url"], url)
        self.assertEqual(ready["state"], "running")

        self.client.post(f"/api/v1/runs/{run['id']}/cancel")
        stopped = self.poll(
            f"/api/v1/runs/{run['id']}", lambda body: body["state"] != "running"
        )
        self.assertEqual(stopped["state"], "cancelled")
        self.assertIsNone(stopped["jupyter_url"])

        events = parse_sse(self.client.get(f"/api/v1/runs/{run['id']}/events").text)
        log, ready_event = events[0][2], events[1][2]
        self.assertEqual(log["message"], "[jupyter] ... token [REDACTED].\n")
        self.assertEqual(ready_event["data"]["url"], url)

    def test_invalid_run_reports_field_issues(self):
        response = self.client.post(
            "/api/v1/runs",
            json=run_request(connection={"target": TARGET}, entrypoint="../x"),
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["issues"][0]["field"], "entrypoint")

    def test_execute_streams_events_until_end(self):
        self.connect()

        def script(workflow):
            workflow.emit(EventKind.STEP, "Connecting", step=1)
            workflow.emit(EventKind.PROGRESS, "Executing the Python workflow...")
            workflow.emit(EventKind.COMPLETED, "done", run_dir="/r", log_path="/r/l")

        run = self.start(script)
        self.assertEqual(run["mode"], "execute")
        self.assertEqual(run["target"], TARGET)

        stream = self.client.get(f"/api/v1/runs/{run['id']}/events")
        self.assertEqual(
            stream.headers["content-type"].split(";")[0], "text/event-stream"
        )
        messages = parse_sse(stream.text)
        self.assertEqual(
            [(name, event_id, data.get("kind")) for name, event_id, data in messages],
            [
                ("run", "0", "step"),
                ("run", "1", "progress"),
                ("run", "2", "completed"),
                ("end", None, None),
            ],
        )

        resumed = parse_sse(
            self.client.get(
                f"/api/v1/runs/{run['id']}/events", headers={"Last-Event-ID": "1"}
            ).text
        )
        self.assertEqual([m[1] for m in resumed], ["2", None])

        current = self.client.get("/api/v1/runs/current").json()
        self.assertEqual(current["state"], "completed")
        self.assertEqual(current["result"], {"run_dir": "/r", "log_path": "/r/l"})
        self.assertEqual(current["event_count"], 3)

    def test_confirmation_through_api(self):
        self.connect()

        def script(workflow):
            approved = workflow.confirm(
                ConfirmationRequest(INSTALL_MINIFORGE, "Install?")
            )
            workflow.emit(EventKind.COMPLETED if approved else EventKind.FAILED, "done")

        run = self.start(script)
        pending = self.poll(
            f"/api/v1/runs/{run['id']}", lambda body: body["confirmation"]
        )
        confirmation = pending["confirmation"]
        self.assertEqual(confirmation["key"], INSTALL_MINIFORGE)
        response = self.client.post(
            f"/api/v1/runs/{run['id']}/confirmations/{confirmation['id']}",
            json={"approved": True},
        )
        self.assertEqual(response.status_code, 204)
        finished = self.poll(
            f"/api/v1/runs/{run['id']}", lambda body: body["state"] != "running"
        )
        self.assertEqual(finished["state"], "completed")

    def test_cancel_and_conflicts_while_running(self):
        self.connect()
        started = threading.Event()

        def script(workflow):
            started.set()
            workflow.cancel_token.wait(10)
            workflow.emit(EventKind.CANCELLED, "cancelled")

        run = self.start(script)
        started.wait(5)

        second = self.client.post(
            "/api/v1/runs", json=run_request(connection={"target": TARGET})
        )
        self.assertEqual(second.status_code, 409)
        self.assertEqual(self.client.delete("/api/v1/connection").status_code, 409)

        response = self.client.post(f"/api/v1/runs/{run['id']}/cancel")
        self.assertEqual(response.status_code, 202)
        self.assertTrue(response.json()["cancel_requested"])
        finished = self.poll(
            f"/api/v1/runs/{run['id']}", lambda body: body["state"] != "running"
        )
        self.assertEqual(finished["state"], "cancelled")

    def test_unknown_run(self):
        self.assertEqual(self.client.get("/api/v1/runs/nope").status_code, 404)
        self.assertEqual(self.client.get("/api/v1/runs/current").status_code, 404)

    def test_completed_run_files_are_listed_and_downloaded(self):
        self.connect()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_dir, instance = root / "run", root / "instance"
            run_dir.mkdir()
            instance.mkdir()
            (run_dir / ".floability-remote-run").write_text("run-id\n")
            (run_dir / "execute-command.log").write_text(
                f"[floability] Created instance structure at: {instance}\n"
            )
            workflow = instance / "workflow"
            workflow.mkdir()
            (workflow / "result.csv").write_bytes(b"value,42\n")
            (workflow / "cached.csv").symlink_to(root / "cache.csv")
            logs = instance / "logs"
            logs.mkdir()
            (logs / "workflow.log").write_text("completed\n")

            def script(workflow):
                workflow.emit(
                    EventKind.COMPLETED,
                    "done",
                    run_dir=str(run_dir),
                    log_path=str(run_dir / "execute-command.log"),
                )

            run = self.start(script)
            self.poll(
                f"/api/v1/runs/{run['id']}", lambda body: body["state"] == "completed"
            )
            response = self.client.get(f"/api/v1/runs/{run['id']}/files")
            self.assertEqual(response.status_code, 200, response.text)
            inventory = response.json()
            self.assertTrue(inventory["instance_found"])
            paths = [item["path"] for item in inventory["files"]]
            self.assertEqual(
                paths,
                ["execute-command.log", "logs/workflow.log", "workflow/result.csv"],
            )

            result = next(
                item
                for item in inventory["files"]
                if item["path"] == "workflow/result.csv"
            )
            download = self.client.get(
                f"/api/v1/runs/{run['id']}/files/{result['id']}/download"
            )
            self.assertEqual(download.status_code, 200, download.text)
            self.assertEqual(download.content, b"value,42\n")
            self.assertIn("result.csv", download.headers["content-disposition"])

            missing = self.client.get(
                f"/api/v1/runs/{run['id']}/files/not-a-file/download"
            )
            self.assertEqual(missing.status_code, 409)
            self.assertEqual(missing.json()["error"]["code"], "download_unavailable")

    def test_files_require_a_finished_run_and_live_connection(self):
        self.connect()
        release = threading.Event()

        def script(workflow):
            release.wait(5)
            workflow.emit(EventKind.COMPLETED, "done", run_dir="/r")

        run = self.start(script)
        response = self.client.get(f"/api/v1/runs/{run['id']}/files")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "files_not_ready")
        release.set()
        self.poll(
            f"/api/v1/runs/{run['id']}", lambda body: body["state"] == "completed"
        )
        self.client.delete("/api/v1/connection")
        response = self.client.get(f"/api/v1/runs/{run['id']}/files")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "connection_required")


@requires_web
class ShutdownTests(unittest.TestCase):
    def test_server_shutdown_cancels_active_run_and_disconnects(self):
        from fastapi.testclient import TestClient

        from floability_remote.web.app import WebSettings, create_app

        closed = []

        class RecordingServices:
            shutting_down = False

            def close(self):
                closed.append(True)

        app = create_app(WebSettings(port=1, session_token="t"), RecordingServices())
        with TestClient(app):
            pass
        self.assertEqual(closed, [True])


if __name__ == "__main__":
    unittest.main()
