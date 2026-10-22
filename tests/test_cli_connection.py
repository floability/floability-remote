import contextlib
import io
import unittest
from unittest.mock import patch

from floability_remote.application.contracts import ConnectionResponse, PromptModel
from floability_remote.cli.commands import run_connect, run_disconnect
from floability_remote.cli.parser import build_parser


class FakeDaemonClient:
    def __init__(self, initial):
        self.initial = initial
        self.connect_request = None
        self.answers = []
        self.disconnected = False

    def get_connection(self):
        if self.answers:
            return ConnectionResponse(
                state="connected",
                target="user@login.example.org",
                remote_user="user",
                remote_host="login.example.org",
            )
        return self.initial

    def connect(self, request):
        self.connect_request = request
        return ConnectionResponse(
            state="connecting",
            target=request.target,
            prompt=PromptModel(id="prompt-1", kind="secret", message="Password:"),
        )

    def answer_prompt(self, prompt_id, answer):
        self.answers.append((prompt_id, answer))

    def disconnect(self):
        self.disconnected = True
        return ConnectionResponse(state="disconnected")


class CliConnectionTests(unittest.TestCase):
    def test_connect_relays_secret_and_leaves_daemon_connection_open(self):
        client = FakeDaemonClient(ConnectionResponse(state="disconnected"))
        args = build_parser().parse_args(["connect", "user@login.example.org"])
        output = io.StringIO()

        with (
            patch("floability_remote.daemon.lifecycle.start", return_value=object()),
            patch("floability_remote.daemon.client.DaemonClient", return_value=client),
            patch(
                "floability_remote.cli.commands.getpass.getpass", return_value="secret"
            ),
            patch("floability_remote.cli.commands.time.sleep"),
            contextlib.redirect_stdout(output),
        ):
            result = run_connect(args)

        self.assertEqual(result, 0)
        self.assertEqual(client.connect_request.target, "user@login.example.org")
        self.assertEqual(client.answers, [("prompt-1", "secret")])
        self.assertFalse(client.disconnected)
        self.assertIn("Connected to user@login.example.org", output.getvalue())
        self.assertNotIn("secret", output.getvalue())

    def test_connect_can_take_over_a_prompt_started_by_mcp(self):
        client = FakeDaemonClient(
            ConnectionResponse(
                state="connecting",
                target="user@login.example.org",
                prompt=PromptModel(
                    id="prompt-2", kind="secret", message="Verification code:"
                ),
            )
        )
        args = build_parser().parse_args(["connect", "user@login.example.org"])

        with (
            patch("floability_remote.daemon.lifecycle.start", return_value=object()),
            patch("floability_remote.daemon.client.DaemonClient", return_value=client),
            patch(
                "floability_remote.cli.commands.getpass.getpass", return_value="123456"
            ),
            patch("floability_remote.cli.commands.time.sleep"),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(run_connect(args), 0)

        self.assertIsNone(client.connect_request)
        self.assertEqual(client.answers, [("prompt-2", "123456")])

    def test_disconnect_uses_the_existing_daemon(self):
        client = FakeDaemonClient(ConnectionResponse(state="connected"))
        args = build_parser().parse_args(["disconnect"])
        with (
            patch(
                "floability_remote.daemon.client.DaemonClient.discover",
                return_value=client,
            ),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(run_disconnect(args), 0)
        self.assertTrue(client.disconnected)


if __name__ == "__main__":
    unittest.main()
