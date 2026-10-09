import contextlib
import io
import unittest
from unittest import mock

from floability_remote.cli import terminal_confirmation
from floability_remote.cli_reporter import CliReporter
from floability_remote.errors import RemoteRunError
from floability_remote.events import Event, EventKind
from floability_remote.interaction import INSTALL_MINIFORGE, ConfirmationRequest


EVENTS = (
    Event(EventKind.STEP, "Connecting to host...", step=1, total=5),
    Event(EventKind.DETAIL, "SSH connection established."),
    Event(EventKind.PROGRESS, "Starting JupyterLab..."),
    Event(EventKind.LOG, "raw remote line\n"),
    Event(EventKind.READY, "Jupyter is ready.", data={"url": "http://127.0.0.1:1/lab/?token=t"}),
    Event(EventKind.CANCELLING, "Interrupted. Asking remote Floability to clean up..."),
    Event(EventKind.COMPLETED, "done"),
)


def render(verbose):
    stdout, stderr = io.StringIO(), io.StringIO()
    reporter = CliReporter(verbose)
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        for event in EVENTS:
            reporter.emit(event)
        reporter.emit(Event(EventKind.WARNING, "cleanup was not confirmed."))
    return stdout.getvalue(), stderr.getvalue()


READY_BLOCK = (
    "\n[remote] READY — open this URL in your local browser:\n"
    "http://127.0.0.1:1/lab/?token=t\n"
    "\nPress Ctrl+C here when you are finished.\n\n"
)


class CliReporterTests(unittest.TestCase):
    def test_concise_output(self):
        stdout, stderr = render(verbose=False)
        self.assertEqual(
            stdout,
            "[remote] [1/5] Connecting to host...\n"
            "[remote]       SSH connection established.\n"
            "[remote]       Starting JupyterLab...\n"
            + READY_BLOCK
            + "\n[remote] Interrupted. Asking remote Floability to clean up...\n",
        )
        self.assertEqual(stderr, "[remote] WARNING: cleanup was not confirmed.\n")

    def test_verbose_output_shows_raw_lines_instead_of_progress(self):
        stdout, _ = render(verbose=True)
        self.assertIn("raw remote line\n", stdout)
        self.assertNotIn("Starting JupyterLab", stdout)

    def test_ready_link_is_printed_unredacted(self):
        stdout, _ = render(verbose=False)
        self.assertIn("\nhttp://127.0.0.1:1/lab/?token=t\n", stdout)


class TerminalConfirmationTests(unittest.TestCase):
    request = ConfirmationRequest(INSTALL_MINIFORGE, "Install Miniforge?")

    def test_yes_flag_approves(self):
        self.assertTrue(terminal_confirmation(True)(self.request))

    def test_non_interactive_terminal_explains_yes_flag(self):
        with mock.patch("sys.stdin") as stdin:
            stdin.isatty.return_value = False
            with self.assertRaisesRegex(RemoteRunError, "Re-run with --yes"):
                terminal_confirmation(False)(self.request)

    def test_interactive_answer(self):
        for answer, expected in (("y", True), ("YES", True), ("", False), ("n", False)):
            with self.subTest(answer=answer):
                with mock.patch("sys.stdin") as stdin, mock.patch(
                    "builtins.input", return_value=answer
                ) as prompt:
                    stdin.isatty.return_value = True
                    self.assertEqual(terminal_confirmation(False)(self.request), expected)
                prompt.assert_called_once_with("Install Miniforge? [y/N] ")


if __name__ == "__main__":
    unittest.main()
