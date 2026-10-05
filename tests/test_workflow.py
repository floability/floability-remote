import contextlib
import io
import unittest
from types import SimpleNamespace
from unittest import mock

from floability_remote.cli import build_parser
from floability_remote.errors import RemoteRunError
from floability_remote.models import RemoteProbe
from floability_remote.workflow import RemoteWorkflow


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

    def __init__(self, target, identity_file=None, ssh_options=()):
        self.target = target
        self.started_arguments = None
        self.tunnel = None
        FakeSession.last_instance = self

    def start(self):
        return None

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

    def close(self):
        return None


PROBE = RemoteProbe(
    os_name="Linux",
    architecture="x86_64",
    conda="/opt/conda/bin/conda",
    env_prefix="/opt/conda/envs/floability-env",
    floability_version="0.3.1",
    git_available=True,
    setsid_available=True,
    downloader="curl",
)


class WorkflowTests(unittest.TestCase):
    def arguments(self, command, verbose=False):
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
            status = RemoteWorkflow(self.arguments(command, verbose)).start()
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
                RemoteWorkflow(self.arguments("execute")).start()


if __name__ == "__main__":
    unittest.main()
