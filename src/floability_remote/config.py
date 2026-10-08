"""Typed configuration shared by the CLI and web API."""

import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import List, Optional, Tuple

from .errors import RemoteRunError


SUPPORTED_MODES = ("run", "execute")
SUPPORTED_BATCH_TYPES = ("local", "slurm", "condor", "uge")
DEFAULT_ENV_NAME = "floability-remote-managed"
DEFAULT_REMOTE_ROOT = "~/.cache/floability-remote/runs"
DEFAULT_JUPYTER_PORT = 8888


@dataclass(frozen=True)
class ConnectionConfig:
    """How to reach the remote login node through OpenSSH."""

    target: str
    identity_file: Optional[str] = None
    ssh_options: Tuple[str, ...] = ()


@dataclass(frozen=True)
class EnvironmentConfig:
    """The remote Conda environment that provides the `floability` launcher."""

    env_name: str = DEFAULT_ENV_NAME
    floability_version: str = ""
    conda_executable: str = ""
    reinstall_miniforge: bool = False


@dataclass(frozen=True)
class BackpackSource:
    """A public Git repository and optional branch, tag, or commit."""

    repository: str
    ref: str = ""


@dataclass(frozen=True)
class RunConfig:
    """Everything needed for one remote `floability run` or `execute`."""

    mode: str
    connection: ConnectionConfig
    backpack: BackpackSource
    batch_type: str
    environment: EnvironmentConfig = field(default_factory=EnvironmentConfig)
    entrypoint: str = ""
    remote_root: str = DEFAULT_REMOTE_ROOT
    jupyter_port: int = DEFAULT_JUPYTER_PORT
    local_port: Optional[int] = None


@dataclass(frozen=True)
class ValidationIssue:
    """One invalid field.

    `field` is a dotted RunConfig attribute path such as `connection.target`.
    `message` describes the problem relative to that field, and `flag` is the
    CLI option that sets it, when there is one.
    """

    field: str
    message: str
    flag: Optional[str] = None

    @property
    def cli_message(self) -> str:
        return f"{self.flag} {self.message}" if self.flag else self.message


class ConfigError(RemoteRunError):
    """One or more configuration fields are invalid."""

    def __init__(self, issues: List[ValidationIssue]):
        self.issues = list(issues)
        super().__init__("; ".join(issue.cli_message for issue in self.issues))


def _has_control_characters(value: str) -> bool:
    return any(ord(character) < 32 for character in value)


def valid_port(value: Optional[int]) -> bool:
    return value is None or 1 <= value <= 65535


def run_config_issues(config: RunConfig) -> List[ValidationIssue]:
    """Return every validation problem without contacting a remote host."""
    issues = []

    def problem(name: str, message: str, flag: Optional[str] = None) -> None:
        issues.append(ValidationIssue(name, message, flag))

    if config.mode not in SUPPORTED_MODES:
        problem("mode", f"Mode must be one of: {', '.join(SUPPORTED_MODES)}.")
    if config.batch_type not in SUPPORTED_BATCH_TYPES:
        problem(
            "batch_type",
            f"must be one of: {', '.join(SUPPORTED_BATCH_TYPES)}.",
            "--batch-type",
        )

    issues.extend(_target_issues(config.connection))

    repository = config.backpack.repository
    if (
        not repository.strip()
        or repository.startswith("-")
        or _has_control_characters(repository)
    ):
        problem(
            "backpack.repository", "is not a valid Git repository URL.", "--backpack"
        )

    ref = config.backpack.ref
    if ref.startswith("-") or _has_control_characters(ref):
        problem(
            "backpack.ref",
            "cannot begin with '-' or contain control characters.",
            "--ref",
        )

    if config.entrypoint:
        entrypoint = Path(config.entrypoint)
        if (
            config.entrypoint.startswith("-")
            or entrypoint.is_absolute()
            or ".." in entrypoint.parts
            or _has_control_characters(config.entrypoint)
        ):
            problem(
                "entrypoint",
                "must be a relative filename inside workflow/.",
                "--entrypoint",
            )

    environment = config.environment
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", environment.env_name):
        problem(
            "environment.env_name",
            "may contain letters, numbers, '.', '_', and '-'.",
            "--env-name",
        )
    if environment.floability_version and not re.fullmatch(
        r"[A-Za-z0-9_.+!-]+", environment.floability_version
    ):
        problem(
            "environment.floability_version",
            "contains unsupported characters.",
            "--floability-version",
        )
    if environment.reinstall_miniforge and environment.conda_executable:
        problem(
            "environment.reinstall_miniforge",
            "cannot be combined with --conda-executable.",
            "--reinstall-miniforge",
        )

    if not config.remote_root.strip() or "\n" in config.remote_root:
        problem("remote_root", "must be a non-empty remote path.", "--remote-root")
    if not valid_port(config.jupyter_port):
        problem("jupyter_port", "must be between 1 and 65535.", "--jupyter-port")
    if not valid_port(config.local_port):
        problem("local_port", "must be between 1 and 65535.", "--local-port")

    issues.extend(_identity_issues(config.connection))
    return issues


def _target_issues(connection: ConnectionConfig) -> List[ValidationIssue]:
    target = connection.target
    if target.startswith("-") or not target.strip():
        return [
            ValidationIssue(
                "connection.target",
                "must be an SSH host or user@host, not an option.",
                "--target",
            )
        ]
    if any(character.isspace() or ord(character) < 32 for character in target):
        return [
            ValidationIssue(
                "connection.target",
                "cannot contain whitespace or control characters.",
                "--target",
            )
        ]
    return []


def _identity_issues(connection: ConnectionConfig) -> List[ValidationIssue]:
    if not connection.identity_file:
        return []
    identity = Path(connection.identity_file).expanduser()
    if identity.is_file():
        return []
    return [
        ValidationIssue(
            "connection.identity_file", f"SSH identity file does not exist: {identity}"
        )
    ]


def connection_issues(connection: ConnectionConfig) -> List[ValidationIssue]:
    """Return validation problems for the connection part of a configuration."""
    return _target_issues(connection) + _identity_issues(connection)


def normalize_connection(connection: ConnectionConfig) -> ConnectionConfig:
    if not connection.identity_file:
        return connection
    return replace(
        connection, identity_file=str(Path(connection.identity_file).expanduser())
    )


def validate_connection(connection: ConnectionConfig) -> ConnectionConfig:
    issues = connection_issues(connection)
    if issues:
        raise ConfigError(issues)
    return normalize_connection(connection)


def validate_run_config(config: RunConfig) -> RunConfig:
    """Validate a configuration and return it with local paths normalized."""
    issues = run_config_issues(config)
    if issues:
        raise ConfigError(issues)
    return replace(config, connection=normalize_connection(config.connection))
