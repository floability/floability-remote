import argparse
import io
import unittest
from contextlib import redirect_stdout
from unittest import mock

from floability_remote.application.contracts import InteractiveUrlResponse
from floability_remote.cli.commands import run_session
from floability_remote.cli.parser import build_parser


class CliSessionTests(unittest.TestCase):
    def test_parser_accepts_trusted_session_open_command(self):
        args = build_parser().parse_args(["session", "open", "run-1", "--no-browser"])
        self.assertEqual((args.command, args.session_command), ("session", "open"))
        self.assertEqual(args.run_id, "run-1")
        self.assertTrue(args.no_browser)

    @mock.patch("floability_remote.cli.commands.webbrowser.open")
    @mock.patch("floability_remote.daemon.client.DaemonClient.discover")
    def test_open_prints_secret_url_without_model_or_browser(self, discover, opened):
        client = discover.return_value
        client.get_interactive_url.return_value = InteractiveUrlResponse(
            run_id="run-1",
            url="http://127.0.0.1:49172/lab/?token=private",
        )
        args = argparse.Namespace(
            command="session",
            session_command="open",
            run_id="run-1",
            no_browser=True,
        )

        output = io.StringIO()
        with redirect_stdout(output):
            result = run_session(args)

        self.assertEqual(result, 0)
        self.assertIn("Open Jupyter: http://127.0.0.1:49172", output.getvalue())
        client.get_interactive_url.assert_called_once_with("run-1")
        opened.assert_not_called()


if __name__ == "__main__":
    unittest.main()
