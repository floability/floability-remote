"""Read-only readiness checks for a connected remote cluster."""

from dataclasses import dataclass
import re
from typing import Optional, Tuple

from . import remote_scripts
from .config import DEFAULT_BASE_DIR, EnvironmentConfig
from .errors import RemoteRunError
from .models import RemoteProbe
from .output import marker_values, parse_probe
from .ssh import SSHSession


@dataclass(frozen=True)
class ClusterReport:
    """Tools, environment, and storage observed on one login node."""

    remote_user: str
    remote_host: str
    probe: RemoteProbe
    env_name: str
    requested_base_dir: str
    resolved_base_dir: str
    storage_path: str
    total_bytes: Optional[int]
    free_bytes: Optional[int]
    quota_status: str
    quota_summary: str
    issues: Tuple[str, ...]

    @property
    def ready(self) -> bool:
        return not self.issues


def validate_cluster_settings(environment: EnvironmentConfig, base_dir: str) -> None:
    """Validate the small subset of run settings used by a readiness check."""
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", environment.env_name):
        raise RemoteRunError(
            "--env-name may contain letters, numbers, '.', '_', and '-'."
        )
    if environment.floability_version and not re.fullmatch(
        r"[A-Za-z0-9_.+!-]+", environment.floability_version
    ):
        raise RemoteRunError("--floability-version contains unsupported characters.")
    for name, value in (
        ("--conda-executable", environment.conda_executable),
        ("--base-dir", base_dir),
    ):
        if value and (
            not value.strip()
            or value.startswith("-")
            or any(ord(character) < 32 for character in value)
        ):
            raise RemoteRunError(
                f"{name} must be a remote path and cannot begin with '-' "
                "or contain control characters."
            )


class ClusterService:
    """Inspect a cluster without installing software or creating directories."""

    def __init__(self, session: SSHSession):
        self.session = session

    def check(
        self, environment: EnvironmentConfig, base_dir: str = ""
    ) -> ClusterReport:
        validate_cluster_settings(environment, base_dir)
        requested_base_dir = base_dir or DEFAULT_BASE_DIR

        probe_result = self.session.run_script(
            remote_scripts.PROBE,
            (environment.env_name, environment.conda_executable),
        )
        probe = parse_probe(probe_result.stdout or "")
        storage_result = self.session.run_script(
            remote_scripts.CHECK_CLUSTER_STORAGE,
            (requested_base_dir,),
        )
        values = marker_values(storage_result.stdout or "")

        def required(key: str) -> str:
            value = values.get(key)
            if value is None:
                raise RemoteRunError(
                    "The cluster check returned incomplete information."
                )
            return value

        def optional_integer(key: str) -> Optional[int]:
            value = required(key).strip()
            if not value:
                return None
            try:
                return int(value)
            except ValueError as error:
                raise RemoteRunError(
                    "The cluster check returned invalid storage information."
                ) from error

        issues = []
        if probe.os_name != "Linux":
            issues.append(f"Remote operating system is {probe.os_name}, not Linux.")
        if not probe.git_available:
            issues.append("Git is not available.")
        if not probe.setsid_available:
            issues.append("setsid is not available.")
        if not probe.conda:
            issues.append("Conda is not installed or could not be discovered.")
        elif not probe.env_prefix:
            issues.append(f"Environment '{environment.env_name}' was not found.")
        elif not probe.floability_version:
            issues.append(
                f"Environment '{environment.env_name}' does not provide Floability."
            )
        elif (
            environment.floability_version
            and probe.floability_version != environment.floability_version
        ):
            issues.append(
                f"Installed Floability {probe.floability_version} does not match "
                f"requested {environment.floability_version}."
            )

        return ClusterReport(
            remote_user=required("__FLOABILITY_REMOTE_CLUSTER_USER__"),
            remote_host=required("__FLOABILITY_REMOTE_CLUSTER_HOST__"),
            probe=probe,
            env_name=environment.env_name,
            requested_base_dir=requested_base_dir,
            resolved_base_dir=required("__FLOABILITY_REMOTE_CLUSTER_BASE_DIR__"),
            storage_path=required("__FLOABILITY_REMOTE_CLUSTER_STORAGE_PATH__"),
            total_bytes=optional_integer("__FLOABILITY_REMOTE_CLUSTER_TOTAL_BYTES__"),
            free_bytes=optional_integer("__FLOABILITY_REMOTE_CLUSTER_FREE_BYTES__"),
            quota_status=required("__FLOABILITY_REMOTE_CLUSTER_QUOTA_STATUS__"),
            quota_summary=required("__FLOABILITY_REMOTE_CLUSTER_QUOTA_SUMMARY__"),
            issues=tuple(issues),
        )
