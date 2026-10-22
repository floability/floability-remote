"""Thin terminal command adapters over shared application operations."""

import argparse
import getpass
import shlex
import sys
import time
import webbrowser
from pathlib import Path

from ..application.errors import OperationError
from ..application.contracts import ConnectRequest
from ..application.audit import AuditRecorder
from ..application.operations.backpacks import clone_repository
from ..application.operations.clusters import check_with_session
from ..application.operations.results import (
    download_with_session,
    list_files_with_session,
    taskvine_with_session,
)
from ..domain.config import (
    ConfigError,
    ConnectionConfig,
    EnvironmentConfig,
    validate_connection,
)
from ..domain.errors import RemoteRunError
from ..services.files import format_size, select_file
from ..remote.ssh import SSHSession
from ..deployment.workflow import RemoteWorkflow
from .reporter import CliReporter
from .parser import validate_args
from .prompts import terminal_confirmation
from .render import print_inventory, print_taskvine_status, select_file_interactively


def _connection(args: argparse.Namespace) -> ConnectionConfig:
    return validate_connection(
        ConnectionConfig(
            target=args.target,
            identity_file=args.identity_file,
            ssh_options=tuple(args.ssh_option),
        )
    )


def _session(connection: ConnectionConfig) -> SSHSession:
    # Resolve through the package export so old test/caller patches keep working.
    from . import SSHSession as Session

    return Session(
        connection.target,
        identity_file=connection.identity_file,
        ssh_options=connection.ssh_options,
    )


def _check_run_dir(run_dir: str) -> None:
    if (
        not run_dir.strip()
        or run_dir.startswith("-")
        or any(ord(c) < 32 for c in run_dir)
    ):
        raise RemoteRunError("--run-dir must be a non-empty remote path.")


def _download_destination(raw: str, filename: str) -> Path:
    destination = Path(raw).expanduser() if raw else Path.cwd()
    return destination / filename if destination.is_dir() else destination


def run_web(args: argparse.Namespace) -> int:
    try:
        from ..daemon.server import serve
    except ImportError as error:
        raise RemoteRunError(
            f"The web interface dependencies are missing ({error.name}). "
            "Reinstall floability-remote to restore them."
        ) from error
    return serve(
        port=args.port,
        open_browser=not args.no_browser,
        idle_timeout=getattr(args, "idle_timeout", None),
    )


def run_daemon(args: argparse.Namespace) -> int:
    from ..daemon.lifecycle import start, status, stop

    if args.daemon_command == "start":
        descriptor = start(
            port=args.port,
            open_browser=not args.no_browser,
            idle_timeout=args.idle_timeout,
        )
        print(f"Daemon running at {descriptor.base_url} (pid {descriptor.pid})")
        return 0
    if args.daemon_command == "status":
        descriptor = status()
        if descriptor is None:
            print("Floability Remote daemon is not running.")
            return 1
        print(f"Daemon running at {descriptor.base_url} (pid {descriptor.pid})")
        return 0
    stop(force=args.yes)
    print("Floability Remote daemon stopped.")
    return 0


def run_connect(args: argparse.Namespace) -> int:
    """Open one daemon-owned SSH session and relay secrets only in this terminal."""
    from ..daemon.client import DaemonClient
    from ..daemon.lifecycle import start

    descriptor = start(open_browser=False)
    client = DaemonClient(descriptor, client="cli-connect")
    connection = client.get_connection()

    if connection.state == "connected":
        if connection.target != args.target:
            raise OperationError(
                "connection_conflict",
                f"Already connected to {connection.target}; disconnect first.",
                status=409,
            )
        print(f"Already connected to {connection.target}.")
        return 0
    if connection.state == "connecting" and connection.target != args.target:
        raise OperationError(
            "connection_conflict",
            f"Already connecting to {connection.target}; disconnect first.",
            status=409,
        )
    if connection.state == "failed":
        client.disconnect()
        connection = client.get_connection()
    if connection.state == "disconnected":
        connection = client.connect(
            ConnectRequest(target=args.target, identity_file=args.identity_file)
        )

    print(f"Connecting to {args.target}...")
    deadline = time.monotonic() + 310
    handled_prompt = ""
    while connection.state == "connecting":
        prompt = connection.prompt
        if prompt is not None and prompt.id != handled_prompt:
            handled_prompt = prompt.id
            try:
                if prompt.kind == "confirm":
                    answer = input(f"[ssh] {prompt.message} ").strip()
                else:
                    answer = getpass.getpass(f"[ssh] {prompt.message} ")
            except (EOFError, KeyboardInterrupt):
                client.answer_prompt(prompt.id, None)
                raise OperationError(
                    "authentication_cancelled", "SSH authentication was cancelled."
                )
            client.answer_prompt(prompt.id, answer)
        if time.monotonic() >= deadline:
            client.disconnect()
            raise OperationError(
                "connection_timeout",
                "SSH connection did not finish within five minutes.",
            )
        time.sleep(0.1)
        connection = client.get_connection()

    if connection.state != "connected":
        raise OperationError(
            "connection_failed", connection.error or "SSH connection failed."
        )
    identity = "@".join(
        item for item in (connection.remote_user, connection.remote_host) if item
    )
    print(f"Connected to {identity or connection.target}.")
    print("The daemon will reuse this SSH connection for later tools.")
    return 0


def run_disconnect(args: argparse.Namespace) -> int:
    from ..daemon.client import DaemonClient

    del args
    connection = DaemonClient.discover(client="cli-connect").disconnect()
    print(f"Connection state: {connection.state}")
    return 0


def run_session(args: argparse.Namespace) -> int:
    """Retrieve a Jupyter URL through the trusted local daemon control plane."""
    from ..daemon.client import DaemonClient

    if args.session_command != "open":
        raise OperationError("unknown_session_command", args.session_command)
    response = DaemonClient.discover(client="cli-session").get_interactive_url(
        args.run_id
    )
    print(f"Open Jupyter: {response.url}")
    if not args.no_browser:
        webbrowser.open(response.url)
    return 0


def run_mcp(args: argparse.Namespace) -> int:
    """Run the optional stdio MCP adapter without polluting protocol stdout."""
    try:
        from ..mcp.server import run
    except ImportError as error:
        if error.name and (error.name == "mcp" or error.name.startswith("mcp.")):
            raise RemoteRunError(
                "The MCP dependencies are not installed. "
                "Install them with `pip install 'floability-remote[mcp]'`."
            ) from error
        raise
    run()
    return 0


def run_agent(args: argparse.Namespace) -> int:
    """Run the optional PydanticAI terminal client."""
    try:
        from ..agent.main import run

        return run(args)
    except ImportError as error:
        optional_roots = {
            "pydantic_ai",
            "fastmcp",
            "mcp",
            "openai",
            "anthropic",
        }
        root = (error.name or "").split(".", 1)[0]
        if root in optional_roots:
            raise RemoteRunError(
                "The agent dependencies are not installed. "
                "Install them with `pip install 'floability-remote[agent]'`."
            ) from error
        raise


def run_download(args: argparse.Namespace) -> int:
    _check_run_dir(args.run_dir)
    connection = _connection(args)
    session = _session(connection)
    try:
        print(f"[remote] Connecting to {connection.target}...")
        session.start()
        from . import FileService as Service

        service = Service(session)  # compatibility hook for existing callers/tests
        inventory = list_files_with_session(session, args.run_dir, service=service)
        selectable = print_inventory(inventory)
        if args.list_only:
            return 0
        item = (
            select_file(inventory, args.file)
            if args.file
            else select_file_interactively(selectable)
        )
        destination = _download_destination(args.output, item.filename)
        # Keep the compatibility service instance above while routing through
        # the shared operation used by future interfaces.
        download_with_session(
            session, args.run_dir, item.id, destination, service=service
        )
        print(f"\nDownloaded {item.path} to {destination}")
        return 0
    finally:
        session.close()


def run_taskvine(args: argparse.Namespace) -> int:
    _check_run_dir(args.run_dir)
    connection = _connection(args)
    session = _session(connection)
    try:
        print(f"[remote] Connecting to {connection.target}...")
        session.start()
        print_taskvine_status(taskvine_with_session(session, args.run_dir))
        return 0
    finally:
        session.close()


def run_cluster_check(args: argparse.Namespace) -> int:
    connection = _connection(args)
    environment = EnvironmentConfig(
        env_name=args.env_name,
        floability_version=args.floability_version,
        conda_executable=args.conda_executable,
    )
    session = _session(connection)
    try:
        print(f"[remote] Connecting to {connection.target}...")
        session.start()
        report = check_with_session(session, environment, args.base_dir)
        print(f"\nRemote host: {report.remote_user}@{report.remote_host}")
        print(f"Status: {'Ready' if report.ready else 'Setup required'}")
        if report.floability_version:
            label = f"{report.env_name} (Floability {report.floability_version})"
        elif report.env_prefix:
            label = f"{report.env_name} (Floability not found)"
        else:
            label = f"{report.env_name} (environment not found)"
        print(f"Environment: {label}")
        print(f"Base directory: {report.resolved_base_dir}")
        if report.total_bytes is not None and report.free_bytes is not None:
            print(
                f"Free space: {format_size(report.free_bytes)} of {format_size(report.total_bytes)}"
            )
        else:
            print("Free space: Not reported")
        if report.quota_status == "reported":
            print(f"Quota: {report.quota_summary}")
        else:
            print("Quota: Not reported by this cluster")
        for issue in report.issues:
            print(f"  - {issue}")
        return 0 if report.ready else 1
    finally:
        session.close()


def run_clone(args: argparse.Namespace) -> int:
    result = clone_repository(args.repository, args.ref, args.into)
    print(f"Backpack copied to {result.path}")
    if result.provenance.commit:
        print(f"Commit: {result.provenance.commit}")
    print("Run it with --backpack " + shlex.quote(result.path))
    return 0


def _dispatch(args: argparse.Namespace) -> int:
    if args.command == "web":
        return run_web(args)
    if args.command == "daemon":
        return run_daemon(args)
    if args.command == "connect":
        return run_connect(args)
    if args.command == "disconnect":
        return run_disconnect(args)
    if args.command == "session":
        return run_session(args)
    if args.command == "mcp":
        return run_mcp(args)
    if args.command == "agent":
        return run_agent(args)
    if args.command == "download":
        return run_download(args)
    if args.command == "check-cluster":
        return run_cluster_check(args)
    if args.command == "taskvine":
        return run_taskvine(args)
    if args.command == "clone":
        return run_clone(args)
    config = validate_args(args)
    return RemoteWorkflow(
        config,
        CliReporter(args.verbose),
        logs_visible=args.verbose,
        confirm=terminal_confirmation(args.yes),
    ).start()


def dispatch(args: argparse.Namespace) -> int:
    target = getattr(args, "target", "")
    try:
        with AuditRecorder().operation(
            f"cli.{args.command}",
            client="cli",
            target=target,
            inputs={"command": args.command},
        ) as audit:
            result = _dispatch(args)
            if result:
                audit.outcome("failed", code=f"exit_{result}")
            return result
    except ConfigError as error:
        for issue in error.issues:
            print(f"[remote] ERROR: {issue.cli_message}", file=sys.stderr)
        return 1
    except (RemoteRunError, OperationError) as error:
        print(f"[remote] ERROR: {error}", file=sys.stderr)
        return 1
