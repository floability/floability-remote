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
from floability_remote.domain.interaction import ConfirmationRequest, INSTALL_MINIFORGE
from floability_remote.remote.connection import ConnectionSnapshot

from .support import make_client, requires_web


TARGET = "user@login.example.org"


class ConnectedSession:
    def __init__(self):
        self.connected_at = 42.0

    def snapshot(self):
        return ConnectionSnapshot(
            state="connected",
            target=TARGET,
            remote_user="user",
            remote_host="login.example.org",
            connected_at=self.connected_at,
        )

    def session_for(self, target):
        if target != TARGET:
            raise AssertionError(target)
        return object()

    def close(self):
        pass


class LifecycleWorkflow:
    behavior = None

    def __init__(self, config, sink, *, confirm, cancel_token, redactor, session):
        self.config = config
        self.sink = sink
        self.confirm = confirm
        self.cancel_token = cancel_token
        self.redactor = redactor
        self.session = session

    def start(self):
        return LifecycleWorkflow.behavior(self)

    def emit(self, kind, message="", **data):
        self.sink.emit(Event(kind, message, data=data))


def wait_until(condition, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.01)
    raise AssertionError("condition was not met")


@requires_web
class Phase4ApiTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.backpack = root / "backpack"
        (self.backpack / "workflow").mkdir(parents=True)
        (self.backpack / "workflow" / "main.py").write_text("print('ok')\n")
        self.connections = ConnectedSession()
        self.planning = PlanningStore(str(root / "plans"))
        self.runs = RunManager(self.connections, workflow_factory=LifecycleWorkflow)
        self.runtime = ApplicationRuntime(
            connections=self.connections,
            runs=self.runs,
            audit=AuditRecorder(root / "audit.jsonl"),
            planning=self.planning,
        )
        self.client = make_client(services=self.runtime)
        source = self.planning.inspect_source(
            BackpackSource(source_type="folder", local_path=str(self.backpack))
        )
        self.config = RunConfig(
            mode="execute",
            connection=ConnectionConfig(TARGET),
            backpack=source.effective,
            batch_type="local",
            entrypoint="main.py",
        )
        self.plan = self.planning.create_plan(source.id, self.config, 42.0)

    def tearDown(self):
        self.runtime.close()
        self.temporary.cleanup()

    def test_start_wait_and_cursor_events_report_retained_paths(self):
        def complete(workflow):
            workflow.redactor.add("secret-value")
            workflow.emit(EventKind.STEP, "Starting")
            workflow.emit(EventKind.LOG, "token=secret-value\n")
            workflow.emit(
                EventKind.COMPLETED,
                "done",
                run_dir="/remote/run-1",
                backpack_dir="/remote/run-1/backpack",
                log_path="/remote/run-1/execute-command.log",
            )

        LifecycleWorkflow.behavior = complete
        started = self.client.post(f"/api/v1/plans/{self.plan.id}/start")
        self.assertEqual(started.status_code, 201, started.text)
        run_id = started.json()["id"]

        page = self.client.get(
            f"/api/v1/runs/{run_id}/wait",
            params={"cursor": 0, "timeout_seconds": 2},
        )
        self.assertEqual(page.status_code, 200, page.text)
        body = page.json()
        self.assertTrue(body["terminal"])
        self.assertEqual(
            [event["kind"] for event in body["events"]], ["step", "completed"]
        )
        self.assertEqual(body["run"]["remote_run_dir"], "/remote/run-1")
        self.assertEqual(
            body["run"]["command_log"], "/remote/run-1/execute-command.log"
        )

        logs = self.client.get(
            f"/api/v1/runs/{run_id}/event-page",
            params={"cursor": 0, "include_logs": True},
        ).json()
        log = next(event for event in logs["events"] if event["kind"] == "log")
        self.assertEqual(log["message"], "token=[REDACTED]\n")
        self.assertEqual(logs["next_cursor"], 3)

        first = self.client.get(
            f"/api/v1/runs/{run_id}/event-page",
            params={"cursor": 0, "limit": 1},
        ).json()
        self.assertEqual(first["next_cursor"], 1)
        self.assertTrue(first["has_more"])
        self.assertFalse(first["terminal"])
        second = self.client.get(
            f"/api/v1/runs/{run_id}/event-page",
            params={"cursor": first["next_cursor"], "limit": 1},
        ).json()
        self.assertEqual(second["next_cursor"], 3)
        self.assertTrue(second["terminal"])

    def test_wait_is_bounded_and_second_run_is_refused(self):
        def block(workflow):
            workflow.cancel_token.wait(5)
            workflow.emit(EventKind.CANCELLED, "cancelled")

        LifecycleWorkflow.behavior = block
        started = self.client.post(f"/api/v1/plans/{self.plan.id}/start")
        run_id = started.json()["id"]
        conflict = self.client.post(f"/api/v1/plans/{self.plan.id}/start")
        self.assertEqual(conflict.status_code, 409)

        page = self.client.get(
            f"/api/v1/runs/{run_id}/wait",
            params={"cursor": 0, "timeout_seconds": 0.02},
        ).json()
        self.assertTrue(page["timed_out"])
        self.assertFalse(page["terminal"])

        cancelled = self.client.post(f"/api/v1/runs/{run_id}/cancel")
        self.assertEqual(cancelled.status_code, 202)
        wait_until(lambda: self.runs.get(run_id).finished)
        self.assertEqual(self.runs.get(run_id).state, "cancelled")

    def test_confirmation_round_trip(self):
        answers = []

        def ask(workflow):
            answers.append(
                workflow.confirm(
                    ConfirmationRequest(INSTALL_MINIFORGE, "Install Miniforge?")
                )
            )
            workflow.emit(EventKind.COMPLETED, "done")

        LifecycleWorkflow.behavior = ask
        run_id = self.client.post(f"/api/v1/plans/{self.plan.id}/start").json()["id"]
        wait_until(lambda: self.runs.get(run_id).confirmation is not None)
        confirmation = self.client.get(f"/api/v1/runs/{run_id}").json()["confirmation"]
        response = self.client.post(
            f"/api/v1/runs/{run_id}/confirmations/{confirmation['id']}",
            json={"approved": True},
        )
        self.assertEqual(response.status_code, 204)
        wait_until(lambda: self.runs.get(run_id).finished)
        self.assertEqual(answers, [True])

    def test_start_accepts_interactive_and_rejects_stale_plans(self):
        def finish(workflow):
            workflow.emit(EventKind.COMPLETED, "interactive ended")

        LifecycleWorkflow.behavior = finish
        interactive = self.planning.create_plan(
            self.plan.source_id,
            replace(self.config, mode="run"),
            42.0,
        )
        response = self.client.post(f"/api/v1/plans/{interactive.id}/start")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["mode"], "run")

        self.connections.connected_at = 43.0
        response = self.client.post(f"/api/v1/plans/{self.plan.id}/start")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "stale_plan")
        self.connections.connected_at = 42.0

        (self.backpack / "workflow" / "main.py").write_text("print('changed')\n")
        response = self.client.post(f"/api/v1/plans/{self.plan.id}/start")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "stale_plan")


if __name__ == "__main__":
    unittest.main()
