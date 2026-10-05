from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class RemoteProbe:
    """Tools and Floability environment discovered on the remote host."""

    os_name: str
    architecture: str
    conda: Optional[str]
    env_prefix: Optional[str]
    floability_version: Optional[str]
    git_available: bool
    setsid_available: bool
    downloader: Optional[str]


@dataclass(frozen=True)
class RemoteWorkspace:
    """Paths owned by one invocation of floability-remote."""

    run_id: str
    run_dir: str
    backpack_dir: str


@dataclass(frozen=True)
class JupyterConnection:
    """Connection details extracted from Floability's Jupyter announcement."""

    remote_port: int
    token: str
    path: str = "/lab/"

