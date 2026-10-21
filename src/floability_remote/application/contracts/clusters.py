"""Remote host readiness-check contracts."""

from typing import List, Optional

from pydantic import BaseModel, Field

from ...domain.config import DEFAULT_ENV_NAME
from .common import StrictModel


class ClusterCheckRequest(StrictModel):
    target: str
    env_name: str = DEFAULT_ENV_NAME
    floability_version: str = ""
    conda_executable: str = ""
    base_dir: str = ""


class ClusterCheckResponse(BaseModel):
    ready: bool
    remote_user: str
    remote_host: str
    os_name: str
    architecture: str
    git_available: bool
    setsid_available: bool
    conda: Optional[str] = None
    env_name: str
    env_prefix: Optional[str] = None
    floability_version: Optional[str] = None
    requested_base_dir: str
    resolved_base_dir: str
    storage_path: str
    total_bytes: Optional[int] = None
    free_bytes: Optional[int] = None
    quota_status: str
    quota_summary: str
    available_batch_types: List[str]
    issues: List[str] = Field(default_factory=list)
