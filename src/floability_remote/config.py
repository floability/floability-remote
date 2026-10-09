"""Typed configuration shared by the CLI and web API."""

import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from .errors import RemoteRunError


SUPPORTED_MODES = ("run", "execute")
SUPPORTED_BATCH_TYPES = ("local", "slurm", "condor", "uge")
DEFAULT_ENV_NAME = "floability-remote-managed"
DEFAULT_REMOTE_ROOT = "~/.cache/floability-remote/runs"
DEFAULT_BASE_DIR = "~/floability-base-dir"
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
class FloabilityOption:
    """An extra `floability run/execute` option, such as `--workers 2`.

    `name` is stored without leading dashes; an empty `value` passes the option
    as a bare flag.
    """

    name: str
    value: str = ""

    @property
    def flag(self) -> str:
        return f"--{self.name}"

    def arguments(self) -> List[str]:
        return [self.flag, self.value] if self.value else [self.flag]


# Options Floability Remote sets itself; use the dedicated settings instead.
MANAGED_FLOABILITY_OPTIONS = {
    "backpack": "the backpack repository",
    "batch-type": "the batch system",
    "entrypoint": "the entrypoint",
    "jupyter-port": "the remote Jupyter port",
    "base-dir": "the Floability base directory",
    "data-cache-dir": "the data cache directory",
}

_OPTION_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_-]*")


def parse_floability_option(name: str, value: str = "") -> FloabilityOption:
    """Accept `workers`, `-workers`, or `--workers`; validation happens later."""
    return FloabilityOption(name=name.strip().lstrip("-"), value=value.strip())


def floability_option_arguments(options: Sequence[FloabilityOption]) -> List[str]:
    """Flatten options into the argument list appended to `floability`."""
    arguments: List[str] = []
    for option in options:
        arguments.extend(option.arguments())
    return arguments


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
    base_dir: str = ""
    data_cache_dir: str = ""
    jupyter_port: int = DEFAULT_JUPYTER_PORT
    local_port: Optional[int] = None
    floability_options: Tuple[FloabilityOption, ...] = ()


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
    for field, value, flag in (
        ("base_dir", config.base_dir, "--base-dir"),
        ("data_cache_dir", config.data_cache_dir, "--data-cache-dir"),
    ):
        if value and (
            not value.strip() or value.startswith("-") or _has_control_characters(value)
        ):
            problem(
                field,
                "must be a remote path and cannot begin with '-' or contain control characters.",
                flag,
            )
    issues.extend(floability_option_issues(config.floability_options))
    if not valid_port(config.jupyter_port):
        problem("jupyter_port", "must be between 1 and 65535.", "--jupyter-port")
    if not valid_port(config.local_port):
        problem("local_port", "must be between 1 and 65535.", "--local-port")

    issues.extend(_identity_issues(config.connection))
    return issues


def floability_option_issues(
    options: Sequence[FloabilityOption],
) -> List[ValidationIssue]:
    """Validate extra Floability options; fields are `floability_options.N.*`."""
    issues = []
    for index, option in enumerate(options):
        prefix = f"floability_options.{index}"
        if not _OPTION_NAME.fullmatch(option.name):
            issues.append(
                ValidationIssue(
                    f"{prefix}.name",
                    f"Floability option '{option.name}' must be a name such as "
                    "'workers' (letters, numbers, '-' and '_').",
                )
            )
        elif option.name in MANAGED_FLOABILITY_OPTIONS:
            issues.append(
                ValidationIssue(
                    f"{prefix}.name",
                    f"Floability option '{option.flag}' is set by Floability Remote; "
                    f"use the setting for {MANAGED_FLOABILITY_OPTIONS[option.name]}.",
                )
            )
        if _has_control_characters(option.value):
            issues.append(
                ValidationIssue(
                    f"{prefix}.value",
                    f"The value for Floability option '{option.flag}' cannot contain "
                    "control characters.",
                )
            )
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
