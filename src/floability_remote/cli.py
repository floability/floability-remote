"""Command-line interface for floability-remote."""

import argparse
import re
import sys
from pathlib import Path
from typing import Optional, Sequence

from . import __version__
from .errors import RemoteRunError
from .workflow import RemoteWorkflow


SUPPORTED_BATCH_TYPES = ("local", "slurm", "condor", "uge")
DEFAULT_REMOTE_ROOT = "~/.cache/floability-remote/runs"


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
        default=8888,
        help="Requested remote Jupyter port (default: 8888).",
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
    execute_parser.set_defaults(jupyter_port=8888, local_port=None)
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
        "--env-name", default="floability-env", help="Remote Conda environment name."
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


def validate_args(args: argparse.Namespace) -> None:
    if args.target.startswith("-") or not args.target.strip():
        raise RemoteRunError("--target must be an SSH host or user@host, not an option.")
    if any(character.isspace() or ord(character) < 32 for character in args.target):
        raise RemoteRunError("--target cannot contain whitespace or control characters.")
    if args.backpack.startswith("-") or any(
        ord(character) < 32 for character in args.backpack
    ):
        raise RemoteRunError("--backpack is not a valid Git repository URL.")
    if args.ref.startswith("-") or any(ord(character) < 32 for character in args.ref):
        raise RemoteRunError("--ref cannot begin with '-' or contain control characters.")
    if args.entrypoint:
        entrypoint = Path(args.entrypoint)
        if (
            args.entrypoint.startswith("-")
            or entrypoint.is_absolute()
            or ".." in entrypoint.parts
        ):
            raise RemoteRunError(
                "--entrypoint must be a relative filename inside workflow/."
            )
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", args.env_name):
        raise RemoteRunError("--env-name may contain letters, numbers, '.', '_', and '-'.")
    if args.floability_version and not re.fullmatch(
        r"[A-Za-z0-9_.+!-]+", args.floability_version
    ):
        raise RemoteRunError("--floability-version contains unsupported characters.")
    if args.reinstall_miniforge and args.conda_executable:
        raise RemoteRunError(
            "--reinstall-miniforge cannot be combined with --conda-executable."
        )
    if not args.remote_root.strip() or "\n" in args.remote_root:
        raise RemoteRunError("--remote-root must be a non-empty remote path.")
    if args.identity_file:
        identity = Path(args.identity_file).expanduser()
        if not identity.is_file():
            raise RemoteRunError(f"SSH identity file does not exist: {identity}")
        args.identity_file = str(identity)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        validate_args(args)
        return RemoteWorkflow(args).start()
    except RemoteRunError as error:
        print(f"[remote] ERROR: {error}", file=sys.stderr)
        return 1
