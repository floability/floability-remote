import contextlib
import threading
import time
import unittest
from unittest import mock

from floability_remote.cli import build_parser, validate_args
from floability_remote.connection import ConnectionConflict
from floability_remote.events import Event, EventKind
from floability_remote.interaction import INSTALL_MINIFORGE, ConfirmationRequest
from floability_remote.runs import RunConflict, RunManager, RunState

from test_workflow import PROBE, FakeSession, StoppableSession


def config(mode="execute", target="login.example.org"):
    return validate_args(
        build_parser().parse_args(
            [
                mode,
                "--target",
                target,
                "--backpack",
                "https://github.com/example/backpack.git",
                "--batch-type",
                "slurm",
            ]
        )
    )


class FakeConnections:
    def __init__(self, session=None):
        self.session = session

    def session_for(self, target):
        if self.session is None:
            raise ConnectionConflict("Connect to the login node before starting a run.")
        return self.session


def wait_for(condition, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.01)
    raise AssertionError("condition was not met")


class ScriptedWorkflow:
    """Minimal workflow driven by a test-supplied function."""

    script = None
    instances = []

    def __init__(self, config, sink, *, confirm, cancel_token, redactor, session):
        self.sink = sink
        self.confirm = confirm
        self.cancel_token = cancel_token
        self.redactor = redactor
        self.session = session
        ScriptedWorkflow.instances.append(self)

    def start(self):
        return ScriptedWorkflow.script(self)

    def emit(self, kind, message="", **data):
        self.sink.emit(Event(kind, message, data=data))


class RunManagerTests(unittest.TestCase):
    def manager(self, session=object()):
        ScriptedWorkflow.instances = []
        return RunManager(FakeConnections(session), workflow_factory=ScriptedWorkflow)

    def test_events_are_recorded_and_secrets_redacted(self):
        def script(workflow):
            workflow.redactor.add("tok3n")
            workflow.emit(EventKind.STEP, "Connecting")
            workflow.emit(EventKind.LOG, "url?token=tok3n\n")
            workflow.emit(EventKind.COMPLETED, "done", run_dir="/r", log_path="/r/x.log")
            return 0

        ScriptedWorkflow.script = script
        session = object()
        manager = self.manager(session)
        run = manager.start(config())
        wait_for(lambda: run.finished)

        self.assertEqual(run.state, RunState.COMPLETED)
        self.assertEqual(run.result, {"run_dir": "/r", "log_path": "/r/x.log"})
        self.assertEqual(run.events_after(1)[0].message, "url?token=[REDACTED]\n")
        self.assertEqual(manager.latest(), run)
        self.assertIs(manager.get(run.id), run)
        self.assertIs(ScriptedWorkflow.instances[0].session, session)

    def test_only_one_active_run(self):
        release = threading.Event()

        def script(workflow):
            release.wait(5)
            workflow.emit(EventKind.COMPLETED, "done")

        ScriptedWorkflow.script = script
        manager = self.manager()
        manager.start(config())
        with self.assertRaises(RunConflict):
            manager.start(config())
        release.set()
        wait_for(lambda: not manager.has_active_run())
        manager.start(config())

    def test_requires_connection(self):
        manager = RunManager(FakeConnections(None), workflow_factory=ScriptedWorkflow)
        with self.assertRaises(ConnectionConflict):
            manager.start(config())
        self.assertIsNone(manager.latest())

    def test_confirmation_round_trip(self):
        answers = []

        def script(workflow):
            answers.append(
                workflow.confirm(ConfirmationRequest(INSTALL_MINIFORGE, "Install Miniforge?"))
            )
            workflow.emit(EventKind.COMPLETED, "done")

        ScriptedWorkflow.script = script
        run = self.manager().start(config())
        wait_for(lambda: run.confirmation is not None)
        pending = run.confirmation
        self.assertEqual((pending.key, pending.message), (INSTALL_MINIFORGE, "Install Miniforge?"))
        self.assertFalse(run.answer_confirmation("stale", True))
        self.assertTrue(run.answer_confirmation(pending.id, True))
        wait_for(lambda: run.finished)
        self.assertEqual(answers, [True])
        kinds = [event.kind for event in run.events_after(0)]
        self.assertEqual(kinds[:2], [EventKind.CONFIRMATION, EventKind.DETAIL])
        self.assertIsNone(run.confirmation)

    def test_cancel_during_confirmation_reports_cancelled(self):
        def script(workflow):
            approved = workflow.confirm(ConfirmationRequest(INSTALL_MINIFORGE, "Install?"))
            kind = EventKind.CANCELLED if workflow.cancel_token.cancelled else EventKind.FAILED
            workflow.emit(kind, f"approved={approved}")

        ScriptedWorkflow.script = script
        manager = self.manager()
        run = manager.start(config())
        wait_for(lambda: run.confirmation is not None)
        manager.cancel(run.id)
        wait_for(lambda: run.finished)
        self.assertEqual(run.state, RunState.CANCELLED)
        self.assertEqual(run.message, "approved=False")
        self.assertTrue(run.cancel_requested)

    def test_wait_for_events(self):
        release = threading.Event()

        def script(workflow):
            release.wait(5)
            workflow.emit(EventKind.COMPLETED, "done")

        ScriptedWorkflow.script = script
        run = self.manager().start(config())
        self.assertFalse(run.wait_for_events(0, timeout=0.05))
        release.set()
        self.assertTrue(run.wait_for_events(0, timeout=5))


class RunManagerWithRealWorkflowTests(unittest.TestCase):
    """RunManager driving the real RemoteWorkflow on a shared session."""

    def run_with(self, session):
        manager = RunManager(FakeConnections(session))
        with contextlib.ExitStack() as stack:
            stack.enter_context(
                mock.patch("floability_remote.workflow.ensure_environment", return_value=PROBE)
            )
            run = manager.start(config())
            return manager, run, stack.pop_all()

    def test_execute_reuses_and_keeps_the_session(self):
        FakeSession.process_output = "[floability] Python script execution\n"
        FakeSession.process_status = 0
        session = FakeSession("login.example.org")
        session.started = True
        manager, run, patches = self.run_with(session)
        with patches:
            wait_for(lambda: run.finished)
        self.assertEqual(run.state, RunState.COMPLETED, run.message)
        messages = [event.message for event in run.events_after(0)]
        self.assertIn("Reusing the open SSH connection.", messages)
        self.assertIn("Executing the Python workflow...", messages)
        self.assertEqual(run.result["log_path"], "/remote/run/execute-command.log")
        self.assertFalse(session.closed)

    def test_cancel_stops_remote_floability(self):
        StoppableSession.lines = ("[floability] Python script execution\n",)
        session = StoppableSession("login.example.org")
        session.started = True
        manager, run, patches = self.run_with(session)
        with patches:
            wait_for(lambda: any(e.kind == EventKind.PROGRESS for e in run.events_after(0)))
            manager.cancel(run.id)
            wait_for(lambda: run.finished)
        self.assertEqual(run.state, RunState.CANCELLED)
        self.assertEqual(session.stop_calls, [("/remote/run", "INT", "45")])
        self.assertFalse(session.closed)


if __name__ == "__main__":
    unittest.main()
