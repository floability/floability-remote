"""Public CLI surface, kept stable while commands are split by responsibility."""

import sys

from ..application.reproduce import cli_arguments, cli_command
from ..remote.ssh import SSHSession
from ..services.files import FileService
from .commands import run_cluster_check, run_clone, run_download, run_taskvine, run_web
from .main import main
from .parser import build_parser, config_from_args, port_number, validate_args
from .prompts import terminal_confirmation
from .render import print_inventory, print_taskvine_status

__all__ = [
    "build_parser",
    "cli_arguments",
    "cli_command",
    "config_from_args",
    "main",
    "port_number",
    "print_inventory",
    "print_taskvine_status",
    "run_cluster_check",
    "run_clone",
    "run_download",
    "run_taskvine",
    "run_web",
    "terminal_confirmation",
    "validate_args",
    # Temporary compatibility exports for callers that patched the old module.
    "FileService",
    "SSHSession",
]
