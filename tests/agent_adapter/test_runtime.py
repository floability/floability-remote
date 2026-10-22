import importlib.util
import unittest


AGENT_AVAILABLE = all(
    importlib.util.find_spec(name) is not None
    for name in ("fastmcp", "mcp", "pydantic_ai")
)


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
            },
        )
        self.assertIn("get_status", result.output)
        self.assertEqual(result.usage.tool_calls, 1)


if __name__ == "__main__":
    unittest.main()
