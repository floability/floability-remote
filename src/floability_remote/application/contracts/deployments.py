"""Floability deployment request, validation, event, and result contracts."""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from ...domain.config import (
    DEFAULT_ENV_NAME,
    DEFAULT_JUPYTER_PORT,
    DEFAULT_REMOTE_ROOT,
    BackpackSource,
    ConnectionConfig,
    EnvironmentConfig,
    RunConfig,
    parse_floability_option,
)
from .common import IssueModel, StrictModel
from .connections import ConnectionModel


class EnvironmentModel(StrictModel):
    env_name: str = DEFAULT_ENV_NAME
    floability_version: str = ""
    conda_executable: str = ""
    reinstall_miniforge: bool = False


class BackpackModel(StrictModel):
    source_type: str = "git"
    repository: str = ""
    ref: str = ""
    local_path: str = ""


class FloabilityOptionModel(StrictModel):
    """`{"name": "workers", "value": "2"}` becomes `--workers 2`."""

    name: str
    value: str = ""


class RunRequest(StrictModel):
    mode: str
    connection: ConnectionModel
    backpack: BackpackModel
    batch_type: str
    environment: EnvironmentModel = Field(default_factory=EnvironmentModel)
    entrypoint: str = ""
    remote_root: str = DEFAULT_REMOTE_ROOT
    base_dir: str = ""
    data_cache_dir: str = ""
    jupyter_port: int = DEFAULT_JUPYTER_PORT
    local_port: Optional[int] = None
    floability_options: List[FloabilityOptionModel] = Field(default_factory=list)

    def to_config(self) -> RunConfig:
        return RunConfig(
            mode=self.mode,
            connection=ConnectionConfig(
                target=self.connection.target,
                identity_file=self.connection.identity_file or None,
            ),
            backpack=BackpackSource(
                source_type=self.backpack.source_type,
                repository=self.backpack.repository,
                ref=self.backpack.ref,
                local_path=self.backpack.local_path,
            ),
            batch_type=self.batch_type,
            environment=EnvironmentConfig(
                env_name=self.environment.env_name,
                floability_version=self.environment.floability_version,
                conda_executable=self.environment.conda_executable,
                reinstall_miniforge=self.environment.reinstall_miniforge,
            ),
            entrypoint=self.entrypoint,
            remote_root=self.remote_root,
            base_dir=self.base_dir,
            data_cache_dir=self.data_cache_dir,
            jupyter_port=self.jupyter_port,
            local_port=self.local_port,
            floability_options=tuple(
                parse_floability_option(option.name, option.value)
                for option in self.floability_options
            ),
        )


class DeploymentPlanRequest(StrictModel):
    source_id: str
    mode: str
    batch_type: str
    environment: EnvironmentModel = Field(default_factory=EnvironmentModel)
    entrypoint: str = ""
    remote_root: str = DEFAULT_REMOTE_ROOT
    base_dir: str = ""
    data_cache_dir: str = ""
    jupyter_port: int = DEFAULT_JUPYTER_PORT
    local_port: Optional[int] = None
    floability_options: List[FloabilityOptionModel] = Field(default_factory=list)

    def to_config(
        self, connection: ConnectionConfig, backpack: BackpackSource
    ) -> RunConfig:
        return RunConfig(
            mode=self.mode,
            connection=connection,
            backpack=backpack,
            batch_type=self.batch_type,
            environment=EnvironmentConfig(
                env_name=self.environment.env_name,
                floability_version=self.environment.floability_version,
                conda_executable=self.environment.conda_executable,
                reinstall_miniforge=self.environment.reinstall_miniforge,
            ),
            entrypoint=self.entrypoint,
            remote_root=self.remote_root,
            base_dir=self.base_dir,
            data_cache_dir=self.data_cache_dir,
            jupyter_port=self.jupyter_port,
            local_port=self.local_port,
            floability_options=tuple(
                parse_floability_option(option.name, option.value)
                for option in self.floability_options
            ),
        )


class DeploymentPlanResponse(BaseModel):
    valid: bool
    plan_id: Optional[str] = None
    source_id: str
    target: str
    mode: str
    batch_type: str
    entrypoint: str
    source_commit: str = ""
    issues: List[IssueModel] = Field(default_factory=list)
    command: Optional[str] = None
    created_at: Optional[float] = None


class ValidationResponse(BaseModel):
    valid: bool
    issues: List[IssueModel]
    command: Optional[str] = Field(
        None, description="Equivalent CLI command when the configuration is valid."
    )


class EventModel(BaseModel):
    kind: str
    message: str
    step: Optional[int] = None
    total: Optional[int] = None
    data: Dict[str, Any] = Field(default_factory=dict)
    timestamp: float


class ConfirmationModel(BaseModel):
    id: str
    key: str
    message: str


class RunResponse(BaseModel):
    id: str
    mode: str
    state: str = Field(description="running, completed, failed, or cancelled.")
    target: str
    repository: str
    source_type: str = "git"
    local_path: str = ""
    ref: str
    batch_type: str
    entrypoint: str
    created_at: float
    finished_at: Optional[float] = None
    message: Optional[str] = None
    result: Dict[str, str] = Field(default_factory=dict)
    jupyter_url: Optional[str] = Field(
        None,
        description="Local Jupyter link while an interactive session is ready. "
        "Contains the Jupyter token; never log it.",
    )
    confirmation: Optional[ConfirmationModel] = None
    cancel_requested: bool = False
    event_count: int


class ConfirmationAnswer(StrictModel):
    approved: bool
