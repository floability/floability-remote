"""PydanticAI construction around Floability Remote's stdio MCP server."""

import os
import sys
from typing import Any

from .policy import ApprovalPolicy
from .prompts import SYSTEM_INSTRUCTIONS


os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")


def create_toolset(policy: ApprovalPolicy):
    """Launch the installed package's MCP command with this Python interpreter."""
    from fastmcp.client.transports import StdioTransport
    from pydantic_ai.mcp import MCPToolset

    transport = StdioTransport(
        command=sys.executable,
        args=["-m", "floability_remote", "mcp"],
    )
    return MCPToolset(
        transport,
        process_tool_call=policy.process_tool_call,
        include_instructions=True,
        tool_error_behavior="failed",
    )


def create_agent(model: Any, policy: ApprovalPolicy):
    """Build the agent without giving it any non-MCP Floability capabilities."""
    from pydantic_ai import Agent

    return Agent(
        model,
        toolsets=[create_toolset(policy)],
        instructions=SYSTEM_INSTRUCTIONS,
    )
