"""Secure terminal relay for daemon-owned SSH authentication prompts.

The relay talks directly to the loopback daemon API. Prompt answers never pass
through an MCP tool, model request, conversation history, or agent audit output.
"""

import asyncio
import getpass
import sys
from typing import Callable, Optional, TextIO

from ..application.contracts import PromptModel
from ..application.errors import OperationError
from ..daemon.client import DaemonClient


SecretInput = Callable[[str], str]
TextInput = Callable[[str], str]


class ConnectionPromptRelay:
    """Poll the daemon and answer AskPass prompts from the user's terminal."""

    def __init__(
        self,
        *,
        client_factory: Callable[..., DaemonClient] = DaemonClient.discover,
        secret_input: SecretInput = getpass.getpass,
        text_input: TextInput = input,
        output: Optional[TextIO] = None,
        poll_interval: float = 0.1,
    ):
        self.client_factory = client_factory
        self.secret_input = secret_input
        self.text_input = text_input
        self.output = output or sys.stderr
        self.poll_interval = poll_interval
        self._handled_prompt = ""
        self._authorized_target = ""

    def before_tool_call(self, name: str, arguments: dict) -> None:
        """Authorize prompt relay only after the host approved a connection."""
        if name == "connect_remote":
            self._authorized_target = str(arguments.get("target", ""))
            self._handled_prompt = ""
        elif name == "disconnect_remote":
            self._authorized_target = ""
            self._handled_prompt = ""

    async def run(self, stop: asyncio.Event) -> None:
        """Relay prompts until `stop` is set; daemon failures remain non-fatal."""
        while not stop.is_set():
            try:
                client = self.client_factory(
                    client="agent-control", session_id="agent-prompt-relay"
                )
                connection = await asyncio.to_thread(client.get_connection)
            except OperationError:
                await self._pause(stop)
                continue

            prompt = connection.prompt
            authorized = bool(self._authorized_target) and (
                connection.target == self._authorized_target
            )
            if prompt is None or not authorized:
                self._handled_prompt = ""
            elif prompt.id != self._handled_prompt:
                self._handled_prompt = prompt.id
                await self._answer(client, prompt)
            await self._pause(stop)

    async def _answer(self, client: DaemonClient, prompt: PromptModel) -> None:
        try:
            if prompt.kind == "confirm":
                raw = await asyncio.to_thread(
                    self.text_input, f"\n[ssh] {prompt.message} "
                )
                answer = raw.strip()
            else:
                answer = await asyncio.to_thread(
                    self.secret_input, f"\n[ssh] {prompt.message} "
                )
        except (EOFError, KeyboardInterrupt):
            answer = None
            print("\n[ssh] Authentication cancelled.", file=self.output)
        await asyncio.to_thread(client.answer_prompt, prompt.id, answer)

    async def _pause(self, stop: asyncio.Event) -> None:
        try:
            await asyncio.wait_for(stop.wait(), timeout=self.poll_interval)
        except asyncio.TimeoutError:
            pass
