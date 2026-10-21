"""Render a RunConfig as reproducible Floability Remote CLI arguments."""

import shlex
from typing import List

from ..domain.config import DEFAULT_ENV_NAME, DEFAULT_JUPYTER_PORT, DEFAULT_REMOTE_ROOT, RunConfig


def cli_arguments(config: RunConfig) -> List[str]:
    arguments = [
        config.mode,
        "--target",
        config.connection.target,
        "--backpack",
        config.backpack.display_location,
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
    return shlex.join(["floability-remote", *cli_arguments(config)])
