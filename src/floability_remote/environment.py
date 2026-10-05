"""Discovery and setup of the remote Floability environment."""

import sys

from . import remote_scripts
from .errors import RemoteRunError
from .models import RemoteProbe
from .output import Reporter, parse_probe
from .ssh import SSHSession


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


def ensure_environment(session: SSHSession, args, reporter: Reporter) -> RemoteProbe:
    probe = probe_remote(session, args.env_name, args.conda_executable)

    if not probe.conda:
        _confirm_miniforge(args, probe)
        reporter.detail("Conda not found; installing user-scoped Miniforge...")
        session.run_script(
            remote_scripts.INSTALL_MINIFORGE,
            (probe.architecture,),
            show_output=args.verbose,
        )
        probe = probe_remote(session, args.env_name, args.conda_executable)

    requested_version_ready = (
        not args.floability_version
        or probe.floability_version == args.floability_version
    )
    if not probe.env_prefix or not probe.floability_version or not requested_version_ready:
        reporter.detail(f"Preparing Conda environment '{args.env_name}'...")
        session.run_script(
            remote_scripts.PREPARE_ENVIRONMENT,
            (
                probe.conda or "",
                args.env_name,
                args.floability_version,
                probe.env_prefix or "",
            ),
            show_output=args.verbose,
        )
        probe = probe_remote(session, args.env_name, args.conda_executable)

    if not probe.conda or not probe.env_prefix or not probe.floability_version:
        raise RemoteRunError(
            f"Remote environment '{args.env_name}' does not provide Floability."
        )

    reporter.detail(
        f"Environment ready: {probe.env_prefix} ({probe.floability_version})"
    )
    return probe


def _confirm_miniforge(args, probe: RemoteProbe) -> None:
    if not probe.downloader:
        raise RemoteRunError(
            "Conda was not found, and neither curl nor wget is available remotely."
        )
    if args.yes:
        return
    if not sys.stdin.isatty():
        raise RemoteRunError(
            "Conda was not found. Re-run with --yes to install user-scoped Miniforge."
        )

    answer = input(
        "Conda was not found. Install Miniforge under "
        "~/.local/share/floability-remote/miniforge? [y/N] "
    )
    if answer.strip().lower() not in {"y", "yes"}:
        raise RemoteRunError("Remote Miniforge installation was declined.")
