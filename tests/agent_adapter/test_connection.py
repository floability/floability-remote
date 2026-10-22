import asyncio
import io
import unittest

from floability_remote.agent.connection import ConnectionPromptRelay
from floability_remote.application.contracts import PromptModel


class FakeClient:
    def __init__(self):
        self.answers = []

    def answer_prompt(self, prompt_id, answer):
        self.answers.append((prompt_id, answer))


class ConnectionPromptRelayTests(unittest.IsolatedAsyncioTestCase):
    def test_only_the_approved_target_may_relay_prompts(self):
        relay = ConnectionPromptRelay()

        self.assertEqual(relay._authorized_target, "")
        relay.before_tool_call(
            "connect_remote", {"target": "mislam5@cclws23.cse.nd.edu"}
        )
        self.assertEqual(
            relay._authorized_target, "mislam5@cclws23.cse.nd.edu"
        )
        relay.before_tool_call("disconnect_remote", {})
        self.assertEqual(relay._authorized_target, "")

    async def test_secret_answer_goes_directly_to_daemon_client(self):
        client = FakeClient()
        relay = ConnectionPromptRelay(
            secret_input=lambda prompt: "private-answer",
            output=io.StringIO(),
        )

        await relay._answer(
            client,
            PromptModel(id="prompt-1", kind="secret", message="Password:"),
        )

        self.assertEqual(client.answers, [("prompt-1", "private-answer")])

    async def test_confirmation_is_trimmed_and_sent_directly(self):
        client = FakeClient()
        relay = ConnectionPromptRelay(
            text_input=lambda prompt: " yes ",
            output=io.StringIO(),
        )

        await relay._answer(
            client,
            PromptModel(id="prompt-2", kind="confirm", message="Continue?"),
        )

        self.assertEqual(client.answers, [("prompt-2", "yes")])

    async def test_cancelled_terminal_input_cancels_daemon_prompt(self):
        client = FakeClient()

        def cancel(prompt):
            raise EOFError

        output = io.StringIO()
        relay = ConnectionPromptRelay(secret_input=cancel, output=output)
        await relay._answer(
            client,
            PromptModel(id="prompt-3", kind="secret", message="Password:"),
        )

        self.assertEqual(client.answers, [("prompt-3", None)])
        self.assertIn("Authentication cancelled", output.getvalue())


if __name__ == "__main__":
    unittest.main()
