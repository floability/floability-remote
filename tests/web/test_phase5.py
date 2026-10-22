import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path

from floability_remote.application.audit import AuditRecorder
from floability_remote.application.planning import PlanningStore
from floability_remote.application.runtime import ApplicationRuntime
from floability_remote.deployment.runs import RunManager
from floability_remote.domain.config import BackpackSource, ConnectionConfig, RunConfig
from floability_remote.domain.events import Event, EventKind
from floability_remote.remote.connection import ConnectionSnapshot

from .support import make_client, requires_web


TARGET = "user@login.example.org"
TOKEN = "private-jupyter-token"
URL = f"http://127.0.0.1:49172/lab/?token={TOKEN}"


class ConnectedSession:
    def snapshot(self):
        return ConnectionSnapshot(
            state="connected",
            target=TARGET,
            remote_user="user",
            remote_host="login.example.org",
            connected_at=42.0,
        )

    def session_for(self, target):
        if target != TARGET:
            raise AssertionError(target)
        return object()

    def close(self):
        pass


class InteractiveWorkflow:
    def __init__(self, config, sink, *, confirm, cancel_token, redactor, session):
        self.sink = sink
        self.cancel_token = cancel_token
        self.redactor = redactor

    def start(self):
        self.redactor.add(TOKEN)
        self.sink.emit(
            Event(
                EventKind.DETAIL,
                "Backpack ready.",
                data={"backpack_dir": "/remote/run/backpack"},
            )
        )
        self.sink.emit(
            Event(
                EventKind.READY,
                "Jupyter is ready.",
                data={
                    "url": URL,
                    "local_port": 49172,
                    "remote_port": 8888,
                },
            )
        )
        self.cancel_token.wait(5)
        self.sink.emit(
            Event(
                EventKind.CANCELLED,
                "The interactive session was stopped.",
                data={"run_dir": "/remote/run"},
            )
        )


def wait_until(condition, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = condition()
        if value:
            return value
        time.sleep(0.01)
    raise AssertionError("condition was not met")


@requires_web
class Phase5ApiTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        backpack = root / "backpack"
        (backpack / "workflow").mkdir(parents=True)
        (backpack / "workflow" / "main.ipynb").write_text("{}\n")
        self.audit_path = root / "audit.jsonl"
        self.connections = ConnectedSession()
        self.planning = PlanningStore(str(root / "plans"))
        self.runs = RunManager(self.connections, workflow_factory=InteractiveWorkflow)
        self.runtime = ApplicationRuntime(
            connections=self.connections,
            runs=self.runs,
            audit=AuditRecorder(self.audit_path),
            planning=self.planning,
        )
        self.client = make_client(services=self.runtime)
        source = self.planning.inspect_source(
            BackpackSource(source_type="folder", local_path=str(backpack))
        )
        config = RunConfig(
            mode="run",
            connection=ConnectionConfig(TARGET),
            backpack=source.effective,
            batch_type="local",
            entrypoint="main.ipynb",
        )
        self.plan = self.planning.create_plan(source.id, config, 42.0)

    def tearDown(self):
        self.runtime.close()
        self.temporary.cleanup()

    def test_interactive_plan_safe_status_trusted_url_and_cleanup(self):
        response = self.client.post(f"/api/v1/plans/{self.plan.id}/start")
        self.assertEqual(response.status_code, 201, response.text)
        run_id = response.json()["id"]

        session_path = f"/api/v1/runs/{run_id}/interactive"
        ready = wait_until(
            lambda: (
                body
                if (body := self.client.get(session_path).json())["state"] == "ready"
                else None
            )
        )
        self.assertEqual(ready["local_port"], 49172)
        self.assertEqual(ready["remote_port"], 8888)
        self.assertTrue(ready["url_available"])
        self.assertNotIn("url", ready)

        trusted = self.client.get(f"/api/v1/runs/{run_id}/interactive/url").json()
        self.assertEqual(trusted, {"run_id": run_id, "url": URL})

        events = self.client.get(
            f"/api/v1/runs/{run_id}/event-page",
            params={"cursor": 0, "include_logs": True},
        ).json()["events"]
        ready_event = next(event for event in events if event["kind"] == "ready")
        self.assertEqual(
            ready_event["data"], {"local_port": 49172, "remote_port": 8888}
        )
        self.assertNotIn(TOKEN, str(events))

        self.client.post(f"/api/v1/runs/{run_id}/cancel")
        stopped = wait_until(
            lambda: (
                body
                if (body := self.client.get(session_path).json())["state"] == "stopped"
                else None
            )
        )
        self.assertFalse(stopped["tunnel_ready"])
        self.assertFalse(stopped["url_available"])
        unavailable = self.client.get(f"/api/v1/runs/{run_id}/interactive/url")
        self.assertEqual(unavailable.status_code, 409)
        self.assertEqual(unavailable.json()["error"]["code"], "interactive_not_ready")
        self.assertNotIn(TOKEN, self.audit_path.read_text())

    def test_execute_run_is_not_an_interactive_session(self):
        execute = self.planning.create_plan(
            self.plan.source_id,
            replace(self.plan.config, mode="execute", entrypoint="main.py"),
            42.0,
        )
        response = self.client.post(f"/api/v1/plans/{execute.id}/start")
        run_id = response.json()["id"]
        status = self.client.get(f"/api/v1/runs/{run_id}/interactive")
        self.assertEqual(status.status_code, 409)
        self.assertEqual(status.json()["error"]["code"], "not_interactive")


if __name__ == "__main__":
    unittest.main()
