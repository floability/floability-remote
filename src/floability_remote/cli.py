"""Command-line interface for floability-remote."""

import argparse
import shlex
import sys
from typing import List, Optional, Sequence

from . import __version__
from .cli_reporter import CliReporter
from .config import (
    DEFAULT_ENV_NAME,
    DEFAULT_JUPYTER_PORT,
    DEFAULT_REMOTE_ROOT,
    SUPPORTED_BATCH_TYPES,
    BackpackSource,
    ConfigError,
    ConnectionConfig,
    EnvironmentConfig,
    RunConfig,
    validate_run_config,
)
from .errors import RemoteRunError
from .interaction import INSTALL_MINIFORGE, Confirm, ConfirmationRequest
from .workflow import RemoteWorkflow


NONINTERACTIVE_HINTS = {
    INSTALL_MINIFORGE: (
        "Conda was not found. Re-run with --yes to install user-scoped Miniforge."
    ),
}


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
    web_parser.add_argument(
        "--port",
        type=port_number,
        default=None,
        help="Local port for the web interface; an available port is selected when omitted.",
    )
    web_parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Print the sign-in link without opening a browser.",
    )
    return parser


def _add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--target",
        required=True,
        help="OpenSSH alias, hostname, or user@host for the remote login node.",
    )
    parser.add_argument(
        "--backpack", required=True, help="Public Git repository URL to clone."
    )
    parser.add_argument(
        "--batch-type", required=True, choices=SUPPORTED_BATCH_TYPES
    )
    parser.add_argument("--ref", default="", help="Optional Git branch, tag, or commit.")
    parser.add_argument(
        "--entrypoint",
        default="",
        help="Optional filename inside the backpack workflow directory.",
    )
    parser.add_argument(
        "--env-name", default=DEFAULT_ENV_NAME, help="Remote Conda environment name."
    )
    parser.add_argument(
        "--floability-version",
        default="",
        help="Version to install when creating or repairing the environment.",
    )
    parser.add_argument(
        "--conda-executable",
        default="",
        help="Absolute remote Conda path when discovery is insufficient.",
    )
    parser.add_argument(
        "--reinstall-miniforge",
        action="store_true",
        help=(
            "Replace Floability Remote's user-scoped Miniforge installation and "
            "use it even when another Conda installation is available."
        ),
    )
    parser.add_argument(
        "--remote-root",
        default=DEFAULT_REMOTE_ROOT,
        help="Parent directory for remote runs.",
    )
    parser.add_argument("--identity-file", help="Optional local SSH private-key path.")
    parser.add_argument(
        "--ssh-option",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Additional OpenSSH -o option; repeat when needed.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Allow a user-scoped Miniforge installation without prompting.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show output from remote setup commands and Floability.",
    )


def port_number(raw: str) -> int:
    try:
        value = int(raw)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be an integer") from error
    if not 1 <= value <= 65535:
        raise argparse.ArgumentTypeError("must be between 1 and 65535")
    return value


def config_from_args(args: argparse.Namespace) -> RunConfig:
    return RunConfig(
        mode=args.command,
        connection=ConnectionConfig(
            target=args.target,
            identity_file=args.identity_file,
            ssh_options=tuple(args.ssh_option),
        ),
        backpack=BackpackSource(repository=args.backpack, ref=args.ref),
        batch_type=args.batch_type,
        environment=EnvironmentConfig(
            env_name=args.env_name,
            floability_version=args.floability_version,
            conda_executable=args.conda_executable,
            reinstall_miniforge=args.reinstall_miniforge,
        ),
        entrypoint=args.entrypoint,
        remote_root=args.remote_root,
        jupyter_port=args.jupyter_port,
        local_port=args.local_port,
    )


def validate_args(args: argparse.Namespace) -> RunConfig:
    """Convert parsed arguments to a validated RunConfig."""
    return validate_run_config(config_from_args(args))


def cli_arguments(config: RunConfig) -> List[str]:
    """Return the CLI arguments that reproduce `config`, omitting defaults."""
    arguments = [
        config.mode,
        "--target",
        config.connection.target,
        "--backpack",
        config.backpack.repository,
        "--batch-type",
        config.batch_type,
    ]

    def option(flag: str, value, default) -> None:
        if value != default:
            arguments.extend([flag, str(value)])

    option("--ref", config.backpack.ref, "")
    option("--entrypoint", config.entrypoint, "")
    if config.connection.identity_file:
        arguments.extend(["--identity-file", config.connection.identity_file])
    for ssh_option in config.connection.ssh_options:
        arguments.extend(["--ssh-option", ssh_option])

    environment = config.environment
    option("--env-name", environment.env_name, DEFAULT_ENV_NAME)
    option("--floability-version", environment.floability_version, "")
    option("--conda-executable", environment.conda_executable, "")
    if environment.reinstall_miniforge:
        arguments.append("--reinstall-miniforge")
    option("--remote-root", config.remote_root, DEFAULT_REMOTE_ROOT)

    if config.mode == "run":
        option("--jupyter-port", config.jupyter_port, DEFAULT_JUPYTER_PORT)
        if config.local_port is not None:
            arguments.extend(["--local-port", str(config.local_port)])
    return arguments


def cli_command(config: RunConfig) -> str:
    """Return a shell-quoted `floability-remote` command for `config`."""
    return shlex.join(["floability-remote", *cli_arguments(config)])


def terminal_confirmation(assume_yes: bool) -> Confirm:
    """Answer confirmation requests from `--yes` or an interactive terminal."""

    def confirm(request: ConfirmationRequest) -> bool:
        if assume_yes:
            return True
        if not sys.stdin.isatty():
            raise RemoteRunError(
                NONINTERACTIVE_HINTS.get(
                    request.key, f"{request.message} Re-run with --yes to approve."
                )
            )
        answer = input(f"{request.message} [y/N] ")
        return answer.strip().lower() in {"y", "yes"}

    return confirm


def run_web(args: argparse.Namespace) -> int:
    # Imported lazily so `run` and `execute` do not load the web stack.
    try:
        from .web.server import serve
    except ImportError as error:
        raise RemoteRunError(
            f"The web interface dependencies are missing ({error.name}). "
            "Reinstall floability-remote to restore them."
        ) from error
    return serve(port=args.port, open_browser=not args.no_browser)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "web":
            return run_web(args)
        config = validate_args(args)
        workflow = RemoteWorkflow(
            config,
            CliReporter(args.verbose),
            logs_visible=args.verbose,
            confirm=terminal_confirmation(args.yes),
        )
        return workflow.start()
    except ConfigError as error:
        for issue in error.issues:
            print(f"[remote] ERROR: {issue.cli_message}", file=sys.stderr)
        return 1
    except RemoteRunError as error:
        print(f"[remote] ERROR: {error}", file=sys.stderr)
        return 1
