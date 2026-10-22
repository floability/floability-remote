"""Host-side MCP approval and visibility policy."""

import asyncio
from typing import Any, Awaitable, Callable, Dict, Iterable, Optional, TextIO

from ..application.audit import redact


# Keep state changes and access to user-selected local sources behind a policy
# enforced by the terminal host, not by the model.
DEFAULT_APPROVAL_TOOLS = frozenset(
    {
        "connect_remote",
        "disconnect_remote",
        "start_deployment",
        "cancel_run",
        "answer_confirmation",
        "download_file",
        "apply_repair",
    }
)

ToolCall = Callable[[str, Dict[str, Any]], Awaitable[Any]]
InputFunction = Callable[[str], str]


class ApprovalPolicy:
    def __init__(
        self,
        assume_yes: bool = False,
        approval_tools: Optional[Iterable[str]] = None,
        input_function: InputFunction = input,
        output: Optional[TextIO] = None,
    ):
        import sys

        self.assume_yes = assume_yes
        selected_tools = (
            DEFAULT_APPROVAL_TOOLS if approval_tools is None else approval_tools
        )
        self.approval_tools = frozenset(selected_tools)
        self.input_function = input_function
        self.output = output or sys.stderr

    async def process_tool_call(
        self,
        context,
        call_tool: ToolCall,
        name: str,
        arguments: Dict[str, Any],
    ) -> Any:
        """Show every call and gate state-changing tools outside model control."""
        del context
        visible_arguments = redact(arguments)
        local_source = name == "inspect_backpack" and arguments.get(
            "source_type", "git"
        ) in {"folder", "zip"}
        if (name in self.approval_tools or local_source) and not self.assume_yes:
            prompt = f"\n[approve] {name} {visible_arguments} ? [y/N] "
            answer = await asyncio.to_thread(self.input_function, prompt)
            if answer.strip().lower() not in {"y", "yes"}:
                print(f"[denied] {name}", file=self.output)
                return {
                    "denied": True,
                    "message": "The user denied this action. Ask what to do next.",
                }
        print(f"[tool] {name} {visible_arguments}", file=self.output)
        return await call_tool(name, arguments)
