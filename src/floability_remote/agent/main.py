"""Interactive and one-shot terminal entry point for the built-in agent."""

import argparse
import asyncio
import os
import sys
from typing import Any, List

from ..application.errors import OperationError
from ..daemon.client import DaemonClient
from ..daemon.lifecycle import start as start_daemon
from .connection import ConnectionPromptRelay
from .models import create_model, parse_model
from .policy import ApprovalPolicy
from .runtime import create_agent


os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")


def _usage_line(result: Any) -> str:
    usage = result.usage
    return (
        f"[usage] requests={usage.requests} tool_calls={usage.tool_calls} "
        f"in={usage.input_tokens} out={usage.output_tokens}"
    )


async def _ask(agent, prompt: str, history: List[Any], relay: ConnectionPromptRelay):
    stop_relay = asyncio.Event()
    relay_task = asyncio.create_task(relay.run(stop_relay))
    try:
        result = await agent.run(prompt, message_history=history)
    finally:
        stop_relay.set()
        await relay_task
    print(f"\nagent> {result.output}")
    print(_usage_line(result), file=sys.stderr)
    return result.all_messages()


async def _session_notifier(client: DaemonClient, stop: asyncio.Event) -> None:
    """Print token-bearing Jupyter links without adding them to model history."""
    notified = set()
    while not stop.is_set():
        try:
            run = client.current_run()
            if run.mode == "run" and run.id not in notified:
                session = client.get_interactive_session(run.id)
                if session.url_available:
                    trusted = client.get_interactive_url(run.id)
                    print(f"\n[session] Open Jupyter: {trusted.url}", flush=True)
                    notified.add(run.id)
        except OperationError as error:
            if error.code not in {
                "run_not_found",
                "not_interactive",
                "interactive_not_ready",
                "daemon_unreachable",
            }:
                print(
                    f"\n[session] Unable to read session state: {error}",
                    file=sys.stderr,
                )
        try:
            await asyncio.wait_for(stop.wait(), timeout=0.25)
        except asyncio.TimeoutError:
            pass


async def _chat(args: argparse.Namespace) -> int:
    selection = parse_model(args.model, args.ollama_base_url)
    model = create_model(selection)
    descriptor = start_daemon(open_browser=False)
    print(
        f"Floability Remote agent using {selection.display_name}. "
        f"Daemon: {descriptor.base_url}",
        flush=True,
    )

    policy = ApprovalPolicy(assume_yes=args.yes)
    relay = ConnectionPromptRelay()
    agent = create_agent(model, policy)
    history: List[Any] = []
    notifier_stop = asyncio.Event()
    notifier = asyncio.create_task(
        _session_notifier(
            DaemonClient(descriptor, client="agent-control"), notifier_stop
        )
    )
    try:
        async with agent:
            if args.prompt:
                await _ask(agent, args.prompt, history, relay)
                return 0

            print(
                "Ask about Floability Remote status, or type 'quit' to exit.",
                flush=True,
            )
            while True:
                try:
                    prompt = await asyncio.to_thread(input, "\nyou> ")
                except EOFError:
                    print()
                    return 0
                if prompt.strip().lower() in {"quit", "exit"}:
                    return 0
                if not prompt.strip():
                    continue
                history = await _ask(agent, prompt, history, relay)
    finally:
        notifier_stop.set()
        await notifier


def run(args: argparse.Namespace) -> int:
    try:
        return asyncio.run(_chat(args))
    except KeyboardInterrupt:
        print("\nAgent chat closed.", file=sys.stderr)
        return 130
    except OperationError:
        raise
    except ImportError:
        # The CLI adapter translates missing optional dependencies into the
        # installation command; do not mislabel them as model-service failures.
        raise
    except Exception as error:
        message = str(error).strip() or error.__class__.__name__
        if args.model.startswith("ollama") or ":" not in args.model:
            message += " Check that Ollama is running and that the selected model is installed."
        raise OperationError("agent_failed", message) from error
