import io
import unittest

from floability_remote.agent.policy import ApprovalPolicy


class ApprovalPolicyTests(unittest.IsolatedAsyncioTestCase):
    async def test_read_only_tool_runs_without_prompt(self):
        output = io.StringIO()
        policy = ApprovalPolicy(
            approval_tools={"start_run"},
            input_function=lambda prompt: self.fail(
                "read-only tool requested approval"
            ),
            output=output,
        )

        async def call(name, arguments):
            return {"name": name, "arguments": arguments}

        result = await policy.process_tool_call(
            None, call, "get_status", {"detail": True}
        )

        self.assertEqual(result["name"], "get_status")
        self.assertIn("[tool] get_status", output.getvalue())

    async def test_mutating_tool_is_denied_without_calling_server(self):
        output = io.StringIO()
        called = False

        async def call(name, arguments):
            nonlocal called
            called = True

        policy = ApprovalPolicy(
            approval_tools={"start_run"},
            input_function=lambda prompt: "no",
            output=output,
        )
        result = await policy.process_tool_call(None, call, "start_run", {})

        self.assertFalse(called)
        self.assertTrue(result["denied"])
        self.assertIn("[denied] start_run", output.getvalue())

    async def test_yes_mode_preapproves_mutating_tool(self):
        output = io.StringIO()
        policy = ApprovalPolicy(
            assume_yes=True,
            approval_tools={"start_run"},
            input_function=lambda prompt: self.fail("--yes still prompted"),
            output=output,
        )

        async def call(name, arguments):
            return "started"

        self.assertEqual(
            await policy.process_tool_call(None, call, "start_run", {}),
            "started",
        )

    async def test_before_call_runs_only_after_approval(self):
        observed = []

        async def call(name, arguments):
            observed.append(("called", name, arguments))
            return "ok"

        denied = ApprovalPolicy(
            approval_tools={"connect_remote"},
            input_function=lambda prompt: "no",
            output=io.StringIO(),
            before_call=lambda name, arguments: observed.append(
                ("authorized", name, arguments)
            ),
        )
        await denied.process_tool_call(
            None, call, "connect_remote", {"target": "user@host"}
        )
        self.assertEqual(observed, [])

        approved = ApprovalPolicy(
            approval_tools={"connect_remote"},
            input_function=lambda prompt: "yes",
            output=io.StringIO(),
            before_call=lambda name, arguments: observed.append(
                ("authorized", name, arguments)
            ),
        )
        await approved.process_tool_call(
            None, call, "connect_remote", {"target": "user@host"}
        )
        self.assertEqual(observed[0][0], "authorized")
        self.assertEqual(observed[1][0], "called")

    async def test_visible_arguments_are_redacted_but_server_receives_original(self):
        output = io.StringIO()
        received = None
        policy = ApprovalPolicy(approval_tools=set(), output=output)

        async def call(name, arguments):
            nonlocal received
            received = arguments
            return "ok"

        arguments = {"target": "host", "password": "never-print-this"}
        await policy.process_tool_call(None, call, "future_tool", arguments)

        self.assertEqual(received, arguments)
        self.assertIn("'password': '<redacted>'", output.getvalue())
        self.assertNotIn("never-print-this", output.getvalue())

    async def test_local_backpack_inspection_requires_approval(self):
        output = io.StringIO()
        called = False

        async def call(name, arguments):
            nonlocal called
            called = True

        policy = ApprovalPolicy(
            approval_tools=set(), input_function=lambda prompt: "no", output=output
        )
        result = await policy.process_tool_call(
            None,
            call,
            "inspect_backpack",
            {"source_type": "folder", "local_path": "/private/project"},
        )

        self.assertFalse(called)
        self.assertTrue(result["denied"])
        self.assertIn("inspect_backpack", output.getvalue())

    async def test_phase4_state_changes_use_default_approval(self):
        for tool in (
            "start_deployment",
            "answer_confirmation",
            "cancel_run",
            "read_run_file",
            "download_run_files",
        ):
            with self.subTest(tool=tool):
                called = False

                async def call(name, arguments):
                    nonlocal called
                    called = True

                policy = ApprovalPolicy(
                    input_function=lambda prompt: "no", output=io.StringIO()
                )
                result = await policy.process_tool_call(None, call, tool, {})
                self.assertFalse(called)
                self.assertTrue(result["denied"])


if __name__ == "__main__":
    unittest.main()
