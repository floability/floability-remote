"""Discovery and setup of the remote Floability environment."""

from . import remote_scripts
from .config import EnvironmentConfig
from .errors import RemoteRunError
from .events import Emitter
from .interaction import INSTALL_MINIFORGE, Confirm, ConfirmationRequest, decline
from .models import RemoteProbe
from .output import parse_probe
from .ssh import SSHSession


MANAGED_CONDA = "~/.local/share/floability-remote/miniforge/bin/conda"
MINIFORGE_CONFIRMATION = ConfirmationRequest(
    INSTALL_MINIFORGE,
    "Conda was not found. Install Miniforge under "
    "~/.local/share/floability-remote/miniforge?",
)


def probe_remote(session: SSHSession, env_name: str, conda_executable: str) -> RemoteProbe:
    result = session.run_script(
        remote_scripts.PROBE,
        (env_name, conda_executable),
        check=True,
    )
    probe = parse_probe(result.stdout)
    if probe.os_name != "Linux":
        raise RemoteRunError(
            f"floability-remote supports Linux remote hosts; found {probe.os_name}."
        )
    if not probe.git_available:
        raise RemoteRunError("Git is not installed or not available on the remote PATH.")
    if not probe.setsid_available:
        raise RemoteRunError(
            "The remote host does not provide 'setsid'; it is required for safe cleanup."
        )
    return probe


def ensure_environment(
    session: SSHSession,
    config: EnvironmentConfig,
    emitter: Emitter,
    confirm: Confirm = decline,
) -> RemoteProbe:
    def run_setup_script(script: str, arguments) -> None:
        session.run_script(
            script,
            arguments,
            on_output=emitter.log_block,
            output_in_error=not emitter.logs_visible,
        )

    probe = probe_remote(session, config.env_name, config.conda_executable)

    if config.reinstall_miniforge:
        emitter.detail("Replacing Floability Remote's user-scoped Miniforge...")
        run_setup_script(remote_scripts.INSTALL_MINIFORGE, (probe.architecture, "yes"))
        probe = probe_remote(session, config.env_name, MANAGED_CONDA)
    elif not probe.conda:
        _confirm_miniforge(probe, confirm)
        emitter.detail("Conda not found; installing user-scoped Miniforge...")
        run_setup_script(remote_scripts.INSTALL_MINIFORGE, (probe.architecture, "no"))
        probe = probe_remote(session, config.env_name, MANAGED_CONDA)

    requested_version_ready = (
        not config.floability_version
        or probe.floability_version == config.floability_version
    )
    if not probe.env_prefix or not probe.floability_version or not requested_version_ready:
        emitter.detail(f"Preparing Conda environment '{config.env_name}'...")
        run_setup_script(
            remote_scripts.PREPARE_ENVIRONMENT,
            (
                probe.conda or "",
                config.env_name,
                config.floability_version,
                probe.env_prefix or "",
            ),
        )
        # Keep verifying through the same Conda installation that prepared the
        # environment. In particular, after --reinstall-miniforge the remote
        # shell may still expose an unrelated site Conda first on PATH.
        probe = probe_remote(
            session,
            config.env_name,
            probe.conda or config.conda_executable,
        )

    if not probe.conda or not probe.env_prefix or not probe.floability_version:
        raise RemoteRunError(
            f"Remote environment '{config.env_name}' does not provide Floability."
        )

    emitter.detail(
        f"Environment ready: {probe.env_prefix} ({probe.floability_version})"
    )
    return probe


def _confirm_miniforge(probe: RemoteProbe, confirm: Confirm) -> None:
    if not probe.downloader:
        raise RemoteRunError(
            "Conda was not found, and neither curl nor wget is available remotely."
        )
    if not confirm(MINIFORGE_CONFIRMATION):
        raise RemoteRunError("Remote Miniforge installation was declined.")
