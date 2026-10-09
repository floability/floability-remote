"""Command-line interface for floability-remote."""

import argparse
import shlex
import sys
from pathlib import Path
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
    parse_floability_option,
    validate_run_config,
    validate_connection,
)
from .errors import RemoteRunError
from .files import (
    GROUP_LABELS,
    GROUP_ORDER,
    FileInventory,
    FileService,
    format_size,
    select_file,
)
from .interaction import INSTALL_MINIFORGE, Confirm, ConfirmationRequest
from .ssh import SSHSession
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

    download_parser = commands.add_parser(
        "download", help="List and download files retained by a remote run."
    )
    download_parser.add_argument(
        "--target",
        required=True,
        help="OpenSSH alias, hostname, or user@host for the remote login node.",
    )
    download_parser.add_argument(
        "--run-dir",
        required=True,
        help="Remote run directory reported after execution.",
    )
    download_parser.add_argument(
        "--file",
        default="",
        help="Logical path or file ID; omit to select interactively.",
    )
    download_parser.add_argument(
        "--output",
        default="",
        help="Local filename or directory (default: current directory).",
    )
    download_parser.add_argument(
        "--list-only",
        action="store_true",
        help="List available files without downloading.",
    )
    download_parser.add_argument(
        "--identity-file", help="Optional local SSH private-key path."
    )
    download_parser.add_argument(
        "--ssh-option",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Additional OpenSSH -o option; repeat when needed.",
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
    parser.add_argument("--batch-type", required=True, choices=SUPPORTED_BATCH_TYPES)
    parser.add_argument(
        "--ref", default="", help="Optional Git branch, tag, or commit."
    )
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
    parser.add_argument(
        "--base-dir",
        default="",
        help=(
            "Remote Floability base directory for instances and software caches; "
            "Floability uses ~/floability-base-dir when omitted."
        ),
    )
    parser.add_argument(
        "--data-cache-dir",
        default="",
        help=(
            "Remote Floability data-cache directory; Floability uses "
            "<base-dir>/floability-data-cache when omitted."
        ),
    )
    parser.add_argument(
        "--floability-option",
        action="append",
        default=[],
        metavar="NAME[=VALUE]",
        help=(
            "Extra option passed to floability, such as workers=2 for "
            "'--workers 2'; repeat when needed."
        ),
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
    option("--base-dir", config.base_dir, "")
    option("--data-cache-dir", config.data_cache_dir, "")
    for extra in config.floability_options:
        value = f"{extra.name}={extra.value}" if extra.value else extra.name
        arguments.extend(["--floability-option", value])

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


def print_inventory(inventory: FileInventory) -> List:
    """Print grouped files and return downloadable files in displayed order."""
    selectable = []
    number = 1
    if not inventory.files:
        print("\nNo downloadable files were found.")
    for group in GROUP_ORDER:
        items = [item for item in inventory.files if item.group == group]
        if not items:
            continue
        print(f"\n{GROUP_LABELS[group]}")
        for item in items:
            if item.downloadable:
                print(f"  {number:>3}. {item.path}  ({format_size(item.size)})")
                selectable.append(item)
                number += 1
            else:
                print(f"    - {item.path}  ({format_size(item.size)}; {item.reason})")
    if not inventory.instance_found:
        print(
            "\nThe run did not report a Floability instance; only command logs are shown."
        )
    if inventory.truncated:
        print("\nWarning: the remote file list was limited to 2,000 files.")
    return selectable


def _select_file(files: List):
    if not files:
        raise RemoteRunError("This run has no files within the download limit.")
    if not sys.stdin.isatty():
        raise RemoteRunError(
            "Interactive selection requires a terminal; use --file with a listed path."
        )
    answer = input(f"\nSelect a file to download [1-{len(files)}]: ").strip()
    try:
        index = int(answer)
    except ValueError as error:
        raise RemoteRunError("File selection must be a number.") from error
    if not 1 <= index <= len(files):
        raise RemoteRunError(f"File selection must be between 1 and {len(files)}.")
    return files[index - 1]


def _download_destination(raw: str, filename: str) -> Path:
    destination = Path(raw).expanduser() if raw else Path.cwd()
    if destination.is_dir():
        destination = destination / filename
    return destination


def run_download(args: argparse.Namespace) -> int:
    if (
        not args.run_dir.strip()
        or args.run_dir.startswith("-")
        or any(ord(character) < 32 for character in args.run_dir)
    ):
        raise RemoteRunError("--run-dir must be a non-empty remote path.")
    connection = validate_connection(
        ConnectionConfig(
            target=args.target,
            identity_file=args.identity_file,
            ssh_options=tuple(args.ssh_option),
        )
    )
    session = SSHSession(
        connection.target,
        identity_file=connection.identity_file,
        ssh_options=connection.ssh_options,
    )
    try:
        print(f"[remote] Connecting to {connection.target}...")
        session.start()
        service = FileService(session)
        inventory = service.list_files(args.run_dir)
        selectable = print_inventory(inventory)
        if args.list_only:
            return 0

        if args.file:
            item = select_file(inventory, args.file)
        else:
            item = _select_file(selectable)
        destination = _download_destination(args.output, item.filename)
        service.download(args.run_dir, item.id, destination)
        print(f"\nDownloaded {item.path} to {destination}")
        return 0
    finally:
        session.close()


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "web":
            return run_web(args)
        if args.command == "download":
            return run_download(args)
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
