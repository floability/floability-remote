import asyncio
import io
import importlib.util
import unittest
from contextlib import redirect_stdout

from floability_remote.application.contracts import (
    InteractiveSessionResponse,
    InteractiveUrlResponse,
    RunResponse,
)
from floability_remote.agent.main import _session_notifier


AGENT_AVAILABLE = all(
    importlib.util.find_spec(name) is not None
    for name in ("fastmcp", "mcp", "pydantic_ai")
)


class SessionNotifierTests(unittest.IsolatedAsyncioTestCase):
    async def test_ready_url_is_printed_by_trusted_control_plane(self):
        class Client:
            def current_run(self):
                return RunResponse(
                    id="run-1",
                    mode="run",
                    state="running",
                    target="user@host",
                    repository="repo",
                    ref="",
                    batch_type="local",
                    entrypoint="main.ipynb",
                    created_at=1.0,
                    event_count=1,
                )

            def get_interactive_session(self, run_id):
                return InteractiveSessionResponse(
                    run_id=run_id,
                    state="ready",
                    run_state="running",
                    tunnel_ready=True,
                    local_port=49172,
                    remote_port=8888,
                    url_available=True,
                    message="ready",
                )

            def get_interactive_url(self, run_id):
                return InteractiveUrlResponse(
                    run_id=run_id,
                    url="http://127.0.0.1:49172/lab/?token=private",
                )

        stop = asyncio.Event()
        output = io.StringIO()
        with redirect_stdout(output):
            task = asyncio.create_task(_session_notifier(Client(), stop))
            await asyncio.sleep(0.02)
            stop.set()
            await task

        self.assertEqual(output.getvalue().count("Open Jupyter:"), 1)
        self.assertIn("token=private", output.getvalue())


@unittest.skipUnless(AGENT_AVAILABLE, "install the agent extra for integration tests")
class AgentRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_test_model_sees_and_calls_the_same_mcp_tools(self):
        from pydantic_ai.models.test import TestModel

        from floability_remote.agent.policy import ApprovalPolicy
        from floability_remote.agent.runtime import create_agent

        model = TestModel(call_tools=["get_status"])
        agent = create_agent(model, ApprovalPolicy(assume_yes=True))

        async with agent:
            result = await agent.run("Check Floability Remote status.")

        tools = {
            tool.name for tool in model.last_model_request_parameters.function_tools
        }
        self.assertEqual(
            tools,
            {
                "get_status",
                "get_connection",
                "connect_remote",
                "disconnect_remote",
                "check_remote",
                "inspect_backpack",
                "check_backpack_source",
                "list_backpack_files",
                "read_backpack_file",
                "validate_deployment",
                "start_deployment",
                "get_run",
                "wait_for_run",
                "get_run_events",
                "answer_confirmation",
                "cancel_run",
                "get_interactive_session",
                "get_taskvine_status",
            },
        )
        self.assertIn("get_status", result.output)
        self.assertEqual(result.usage.tool_calls, 1)


if __name__ == "__main__":
    unittest.main()
