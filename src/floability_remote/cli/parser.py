"""Argument parsing and conversion to interface-neutral configuration."""

import argparse
from pathlib import Path

from .. import __version__
from ..domain.config import (
    DEFAULT_ENV_NAME,
    DEFAULT_JUPYTER_PORT,
    DEFAULT_REMOTE_ROOT,
    SUPPORTED_BATCH_TYPES,
    BackpackSource,
    ConnectionConfig,
    EnvironmentConfig,
    RunConfig,
    parse_floability_option,
)
from ..application.validation import validate_run_config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="floability-remote",
        description="Run Floability backpacks on remote Linux systems.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)

    run_parser = commands.add_parser(
        "run", help="Start an interactive remote backpack and Jupyter tunnel."
    )
    _add_common_arguments(run_parser)
    run_parser.add_argument(
        "--jupyter-port",
        type=port_number,
        default=DEFAULT_JUPYTER_PORT,
        help=f"Requested remote Jupyter port (default: {DEFAULT_JUPYTER_PORT}).",
    )
    run_parser.add_argument(
        "--local-port",
        type=port_number,
        default=None,
        help="Local tunnel port; an available port is selected when omitted.",
    )

    execute_parser = commands.add_parser(
        "execute", help="Execute a remote backpack and wait for completion."
    )
    _add_common_arguments(execute_parser)
    execute_parser.set_defaults(jupyter_port=DEFAULT_JUPYTER_PORT, local_port=None)

    web_parser = commands.add_parser(
        "web", help="Start the local web interface on 127.0.0.1."
    )
    web_parser.add_argument("--port", type=port_number, default=None)
    web_parser.add_argument("--no-browser", action="store_true")
    web_parser.add_argument(
        "--idle-timeout", type=float, default=None, help=argparse.SUPPRESS
    )

    daemon_parser = commands.add_parser(
        "daemon", help="Manage the local Floability Remote daemon."
    )
    daemon_commands = daemon_parser.add_subparsers(dest="daemon_command", required=True)
    daemon_start = daemon_commands.add_parser("start", help="Start the daemon in the background.")
    daemon_start.add_argument("--port", type=port_number, default=None)
    daemon_start.add_argument("--no-browser", action="store_true")
    daemon_start.add_argument(
        "--idle-timeout",
        type=float,
        default=3600,
        help="Stop after this many idle seconds (default: 3600; 0 disables).",
    )
    daemon_commands.add_parser("status", help="Show whether the daemon is running.")
    daemon_stop = daemon_commands.add_parser("stop", help="Stop the current daemon.")
    daemon_stop.add_argument(
        "--yes",
        action="store_true",
        help="Allow an active run to be cancelled and cleaned up.",
    )

    download_parser = commands.add_parser(
        "download", help="List and download files retained by a remote run."
    )
    _add_connection_arguments(download_parser)
    download_parser.add_argument("--run-dir", required=True)
    download_parser.add_argument("--file", default="")
    download_parser.add_argument("--output", default="")
    download_parser.add_argument("--list-only", action="store_true")

    taskvine_parser = commands.add_parser(
        "taskvine", help="Show TaskVine activity for a remote run."
    )
    _add_connection_arguments(taskvine_parser)
    taskvine_parser.add_argument("--run-dir", required=True)

    check_parser = commands.add_parser(
        "check-cluster", help="Inspect remote Floability and storage readiness."
    )
    _add_connection_arguments(check_parser)
    check_parser.add_argument("--env-name", default=DEFAULT_ENV_NAME)
    check_parser.add_argument("--floability-version", default="")
    check_parser.add_argument("--conda-executable", default="")
    check_parser.add_argument("--base-dir", default="")

    clone_parser = commands.add_parser(
        "clone", help="Copy a Git backpack to this computer for editing."
    )
    clone_parser.add_argument("repository", help="Git repository URL.")
    clone_parser.add_argument("--ref", default="", help="Branch, tag, or commit.")
    clone_parser.add_argument("--into", default="")
    return parser


def _add_connection_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--target", required=True)
    parser.add_argument("--identity-file")
    parser.add_argument(
        "--ssh-option", action="append", default=[], metavar="KEY=VALUE"
    )


def _add_common_arguments(parser: argparse.ArgumentParser) -> None:
    _add_connection_arguments(parser)
    parser.add_argument(
        "--backpack",
        required=True,
        help="Git repository URL, local backpack folder, or local ZIP file.",
    )
    parser.add_argument("--batch-type", required=True, choices=SUPPORTED_BATCH_TYPES)
    parser.add_argument("--ref", default="")
    parser.add_argument("--entrypoint", default="")
    parser.add_argument("--env-name", default=DEFAULT_ENV_NAME)
    parser.add_argument("--floability-version", default="")
    parser.add_argument("--conda-executable", default="")
    parser.add_argument("--reinstall-miniforge", action="store_true")
    parser.add_argument("--remote-root", default=DEFAULT_REMOTE_ROOT)
    parser.add_argument("--base-dir", default="")
    parser.add_argument("--data-cache-dir", default="")
    parser.add_argument(
        "--floability-option", action="append", default=[], metavar="NAME[=VALUE]"
    )
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--verbose", action="store_true")


def port_number(raw: str) -> int:
    try:
        value = int(raw)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be an integer") from error
    if not 1 <= value <= 65535:
        raise argparse.ArgumentTypeError("must be between 1 and 65535")
    return value


def config_from_args(args: argparse.Namespace) -> RunConfig:
    backpack_path = Path(args.backpack).expanduser()
    path_hint = args.backpack.startswith((".", "~", "/")) or args.backpack.lower().endswith(".zip")
    if backpack_path.exists() or path_hint:
        source_type = "folder" if backpack_path.is_dir() else "zip"
        source = BackpackSource(
            source_type=source_type,
            local_path=str(backpack_path.resolve()),
            ref=args.ref,
        )
    else:
        source = BackpackSource(repository=args.backpack, ref=args.ref)
    return RunConfig(
        mode=args.command,
        connection=ConnectionConfig(
            target=args.target,
            identity_file=args.identity_file,
            ssh_options=tuple(args.ssh_option),
        ),
        backpack=source,
        batch_type=args.batch_type,
        environment=EnvironmentConfig(
            env_name=args.env_name,
            floability_version=args.floability_version,
            conda_executable=args.conda_executable,
            reinstall_miniforge=args.reinstall_miniforge,
        ),
        entrypoint=args.entrypoint,
        remote_root=args.remote_root,
        base_dir=args.base_dir,
        data_cache_dir=args.data_cache_dir,
        jupyter_port=args.jupyter_port,
        local_port=args.local_port,
        floability_options=tuple(
            parse_floability_option(*raw.split("=", 1))
            for raw in args.floability_option
        ),
    )


def validate_args(args: argparse.Namespace) -> RunConfig:
    return validate_run_config(config_from_args(args))
