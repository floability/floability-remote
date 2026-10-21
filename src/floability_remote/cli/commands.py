"""Thin terminal command adapters over shared application operations."""

import argparse
import shlex
import sys
from pathlib import Path

from ..application.errors import OperationError
from ..application.audit import AuditRecorder
from ..application.operations.backpacks import clone_repository
from ..application.operations.clusters import check_with_session
from ..application.operations.results import (
    download_with_session,
    list_files_with_session,
    taskvine_with_session,
)
from ..domain.config import ConfigError, ConnectionConfig, EnvironmentConfig, validate_connection
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
    if not run_dir.strip() or run_dir.startswith("-") or any(ord(c) < 32 for c in run_dir):
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
        item = select_file(inventory, args.file) if args.file else select_file_interactively(selectable)
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
            print(f"Free space: {format_size(report.free_bytes)} of {format_size(report.total_bytes)}")
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
