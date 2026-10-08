import contextlib
import io
import queue
import threading
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from floability_remote import remote_scripts
from floability_remote.cli import build_parser, validate_args
from floability_remote.cli_reporter import CliReporter
from floability_remote.errors import RemoteRunError
from floability_remote.events import EventKind, ListSink, RedactingSink, Redactor
from floability_remote.interaction import CancelToken
from floability_remote.models import RemoteProbe
from floability_remote.workflow import RemoteWorkflow


def cli_workflow(args):
    """Build the workflow exactly as the CLI's main() does."""
    return RemoteWorkflow(
        validate_args(args),
        CliReporter(args.verbose),
        logs_visible=args.verbose,
    )


class FakeProcess:
    def __init__(self, output="", status=0, running=False):
        self.stdout = io.StringIO(output)
        self.status = status
        self.running = running

    def wait(self, timeout=None):
        self.running = False
        return self.status

    def poll(self):
        return None if self.running else self.status

    def terminate(self):
        self.running = False
        self.status = -15

    def kill(self):
        self.running = False
        self.status = -9


class FakeSession:
    process_output = ""
    process_status = 0
    last_instance = None

    def __init__(self, target, identity_file=None, ssh_options=(), **kwargs):
        self.target = target
        self.started = False
        self.closed = False
        self.started_arguments = None
        self.tunnel = None
        FakeSession.last_instance = self

    def start(self):
        self.started = True

    def run_script(self, script, arguments=(), **kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout=(
                "__FLOABILITY_REMOTE_RUN_DIR__=/remote/run\n"
                "__FLOABILITY_REMOTE_BACKPACK__=/remote/run/backpack\n"
            ),
        )

    def start_script(self, script, arguments):
        self.started_arguments = arguments
        return FakeProcess(
            self.process_output,
            status=self.process_status,
            running=True,
        )

    def start_tunnel(self, local_port, remote_port):
        self.tunnel = (local_port, remote_port)
        return FakeProcess(running=True)

    def cancel_tunnel(self, local_port, remote_port):
        self.cancelled_tunnels = getattr(self, "cancelled_tunnels", []) + [
            (local_port, remote_port)
        ]

    def close(self):
        self.closed = True


PROBE = RemoteProbe(
    os_name="Linux",
    architecture="x86_64",
    conda="/opt/conda/bin/conda",
    env_prefix="/opt/conda/envs/floability-remote-managed",
    floability_version="0.3.1",
    git_available=True,
    setsid_available=True,
    downloader="curl",
)


class WorkflowTests(unittest.TestCase):
    def arguments(self, command, verbose=False, *extra):
        arguments = [
            command,
            "--target",
            "login.example.org",
            "--backpack",
            "https://github.com/example/backpack.git",
            "--batch-type",
            "local",
        ]
        if verbose:
            arguments.append("--verbose")
        arguments.extend(extra)
        return build_parser().parse_args(arguments)

    def execute_with_fakes(self, command, output, verbose=False):
        FakeSession.process_output = output
        FakeSession.process_status = 0
        captured = io.StringIO()
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch("floability_remote.workflow.SSHSession", FakeSession))
            stack.enter_context(
                mock.patch("floability_remote.workflow.ensure_environment", return_value=PROBE)
            )
            stack.enter_context(
                mock.patch("floability_remote.workflow.choose_local_port", return_value=49172)
            )
            stack.enter_context(contextlib.redirect_stdout(captured))
            status = cli_workflow(self.arguments(command, verbose)).start()
        self.assertEqual(status, 0)
        return captured.getvalue(), FakeSession.last_instance

    def test_run_opens_tunnel_and_prints_local_url(self):
        output, session = self.execute_with_fakes(
            "run",
            "[floability] JupyterLab startup\n"
            "[jupyter] Detected JupyterLab URL with port 8888 and token abc123.\n",
        )
        self.assertEqual(session.tunnel, (49172, 8888))
        self.assertIn("http://127.0.0.1:49172/lab/?token=abc123", output)
        self.assertEqual(session.started_arguments[4], "run")

    def test_execute_waits_without_opening_tunnel(self):
        output, session = self.execute_with_fakes(
            "execute",
            "raw task output\n[floability] Python script execution\n",
        )
        self.assertIsNone(session.tunnel)
        self.assertEqual(session.started_arguments[4], "execute")
        self.assertIn("Executing the Python workflow...", output)
        self.assertIn("execution completed successfully", output)
        self.assertIn("Remote backpack and outputs: /remote/run/backpack", output)
        self.assertNotIn("raw task output", output)

    def test_floability_cache_directories_are_forwarded(self):
        FakeSession.process_output = ""
        FakeSession.process_status = 0
        with contextlib.ExitStack() as stack:
            stack.enter_context(
                mock.patch("floability_remote.workflow.SSHSession", FakeSession)
            )
            stack.enter_context(
                mock.patch(
                    "floability_remote.workflow.ensure_environment", return_value=PROBE
                )
            )
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            workflow = cli_workflow(
                self.arguments(
                    "execute",
                    False,
                    "--base-dir",
                    "/scratch/floability base",
                    "--data-cache-dir",
                    "/scratch/data-cache",
                )
            )
            workflow.start()

        self.assertEqual(
            FakeSession.last_instance.started_arguments[-2:],
            ("/scratch/floability base", "/scratch/data-cache"),
        )

    def test_verbose_execute_shows_raw_output(self):
        output, _ = self.execute_with_fakes(
            "execute", "raw task output\n", verbose=True
        )
        self.assertIn("raw task output", output)

    def test_failed_execute_reports_remote_log(self):
        FakeSession.process_output = "remote failure\n"
        FakeSession.process_status = 7
        captured = io.StringIO()
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch("floability_remote.workflow.SSHSession", FakeSession))
            stack.enter_context(
                mock.patch("floability_remote.workflow.ensure_environment", return_value=PROBE)
            )
            stack.enter_context(contextlib.redirect_stdout(captured))
            with self.assertRaisesRegex(
                RemoteRunError,
                r"/remote/run/execute-command\.log",
            ):
                cli_workflow(self.arguments("execute")).start()


class BlockingProcess:
    """A remote process whose output stays open until it is stopped."""

    def __init__(self, lines):
        self._lines = queue.Queue()
        for line in lines:
            self._lines.put(line)
        self.stdout = iter(self._lines.get, None)
        self.status = None
        self._finished = threading.Event()

    def finish(self, status):
        self.status = status
        self._lines.put(None)
        self._finished.set()

    def wait(self, timeout=None):
        self._finished.wait(timeout)
        return self.status

    def poll(self):
        return self.status

    def terminate(self):
        self.finish(-15)

    def kill(self):
        self.finish(-9)


class StoppableSession(FakeSession):
    """A session whose remote process exits when STOP_FLOABILITY runs."""

    lines = ()

    def start_script(self, script, arguments):
        self.process = BlockingProcess(self.lines)
        self.stop_calls = []
        return self.process

    def run_script(self, script, arguments=(), **kwargs):
        if script == remote_scripts.STOP_FLOABILITY:
            self.stop_calls.append(tuple(arguments))
            self.process.finish(130)
            return SimpleNamespace(returncode=0, stdout="")
        return super().run_script(script, arguments, **kwargs)


def config(mode="execute"):
    return validate_args(
        build_parser().parse_args(
            [
                mode,
                "--target",
                "login.example.org",
                "--backpack",
                "https://github.com/example/backpack.git",
                "--batch-type",
                "local",
            ]
        )
    )


class StructuredWorkflowTests(unittest.TestCase):
    def patches(self, stack, session_class):
        stack.enter_context(mock.patch("floability_remote.workflow.SSHSession", session_class))
        stack.enter_context(
            mock.patch("floability_remote.workflow.ensure_environment", return_value=PROBE)
        )
        stack.enter_context(
            mock.patch("floability_remote.workflow.choose_local_port", return_value=49172)
        )

    def test_execute_emits_steps_and_terminal_outcome(self):
        FakeSession.process_output = "[floability] Python script execution\n"
        FakeSession.process_status = 0
        sink = ListSink()
        with contextlib.ExitStack() as stack:
            self.patches(stack, FakeSession)
            status = RemoteWorkflow(config(), sink).start()

        self.assertEqual(status, 0)
        steps = [event.step for event in sink.events if event.kind == EventKind.STEP]
        self.assertEqual(steps, [1, 2, 3, 4, 5])
        final = sink.events[-1]
        self.assertEqual(final.kind, EventKind.COMPLETED)
        self.assertEqual(final.data["run_dir"], "/remote/run")
        self.assertEqual(final.data["log_path"], "/remote/run/execute-command.log")

    def test_failure_emits_failed_outcome(self):
        FakeSession.process_output = "remote failure\n"
        FakeSession.process_status = 7
        sink = ListSink()
        with contextlib.ExitStack() as stack:
            self.patches(stack, FakeSession)
            with self.assertRaises(RemoteRunError):
                RemoteWorkflow(config(), sink).start()
        self.assertEqual(sink.events[-1].kind, EventKind.FAILED)
        self.assertIn("exited with status 7", sink.events[-1].message)

    def test_jupyter_token_is_redacted_from_logs_but_not_ready_link(self):
        FakeSession.process_output = (
            "[jupyter] Detected JupyterLab URL with port 8888 and token abc123.\n"
        )
        FakeSession.process_status = 0
        redactor = Redactor()
        sink = ListSink()
        with contextlib.ExitStack() as stack:
            self.patches(stack, FakeSession)
            workflow = RemoteWorkflow(
                config("run"), RedactingSink(sink, redactor), redactor=redactor
            )
            workflow.start()

        logs = [event.message for event in sink.events if event.kind == EventKind.LOG]
        self.assertEqual(
            logs,
            ["[jupyter] Detected JupyterLab URL with port 8888 and token [REDACTED].\n"],
        )
        ready = [event for event in sink.events if event.kind == EventKind.READY]
        self.assertEqual(ready[0].data["url"], "http://127.0.0.1:49172/lab/?token=abc123")
        self.assertEqual(workflow.jupyter_url, ready[0].data["url"])

    def test_cancel_from_another_thread_stops_remote_floability(self):
        StoppableSession.lines = ("[floability] Python script execution\n",)
        sink = ListSink()
        result = {}
        with contextlib.ExitStack() as stack:
            self.patches(stack, StoppableSession)
            workflow = RemoteWorkflow(config(), sink)
            runner = threading.Thread(
                target=lambda: result.setdefault("status", workflow.start())
            )
            runner.start()
            deadline = time.monotonic() + 5
            while EventKind.PROGRESS not in sink.kinds() and time.monotonic() < deadline:
                time.sleep(0.01)
            workflow.cancel()
            runner.join(timeout=5)

        self.assertFalse(runner.is_alive())
        self.assertEqual(result["status"], 130)
        session = StoppableSession.last_instance
        self.assertEqual(session.stop_calls, [("/remote/run", "INT", "45")])
        kinds = sink.kinds()
        self.assertIn(EventKind.CANCELLING, kinds)
        self.assertEqual(kinds[-1], EventKind.CANCELLED)

    def test_shared_session_releases_tunnel_and_stays_open(self):
        FakeSession.process_output = (
            "[jupyter] Detected JupyterLab URL with port 8888 and token abc123.\n"
        )
        FakeSession.process_status = 0
        session = FakeSession("login.example.org")
        session.started = True
        sink = ListSink()
        with contextlib.ExitStack() as stack:
            self.patches(stack, FakeSession)
            status = RemoteWorkflow(config("run"), sink, session=session).start()
        self.assertEqual(status, 0)
        self.assertEqual(session.tunnel, (49172, 8888))
        self.assertEqual(session.cancelled_tunnels, [(49172, 8888)])
        self.assertFalse(session.closed)

    def test_owned_session_is_closed_instead(self):
        FakeSession.process_output = (
            "[jupyter] Detected JupyterLab URL with port 8888 and token abc123.\n"
        )
        FakeSession.process_status = 0
        with contextlib.ExitStack() as stack:
            self.patches(stack, FakeSession)
            RemoteWorkflow(config("run"), ListSink()).start()
        session = FakeSession.last_instance
        self.assertTrue(session.closed)
        self.assertFalse(hasattr(session, "cancelled_tunnels"))

    def test_stopping_ready_session_reports_stopped(self):
        StoppableSession.lines = (
            "[jupyter] Detected JupyterLab URL with port 8888 and token abc123.\n",
        )
        sink = ListSink()
        result = {}
        with contextlib.ExitStack() as stack:
            self.patches(stack, StoppableSession)
            workflow = RemoteWorkflow(config("run"), sink)
            runner = threading.Thread(
                target=lambda: result.setdefault("status", workflow.start())
            )
            runner.start()
            deadline = time.monotonic() + 5
            while EventKind.READY not in sink.kinds() and time.monotonic() < deadline:
                time.sleep(0.01)
            workflow.cancel()
            runner.join(timeout=5)
        self.assertEqual(result["status"], 130)
        self.assertEqual(sink.events[-1].kind, EventKind.CANCELLED)
        self.assertEqual(sink.events[-1].message, "The interactive session was stopped.")

    def test_cancel_before_start_does_not_connect(self):
        token = CancelToken()
        token.cancel()
        sink = ListSink()
        with contextlib.ExitStack() as stack:
            self.patches(stack, FakeSession)
            with mock.patch.object(FakeSession, "start") as start:
                status = RemoteWorkflow(config(), sink, cancel_token=token).start()
        self.assertEqual(status, 130)
        start.assert_not_called()
        self.assertEqual(sink.kinds()[-1], EventKind.CANCELLED)


if __name__ == "__main__":
    unittest.main()
