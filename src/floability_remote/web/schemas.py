"""Pydantic models for API requests and responses.

Request models only describe shape. Business validation stays in
`floability_remote.config` so the CLI and API accept exactly the same values.
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from ..config import (
    DEFAULT_ENV_NAME,
    DEFAULT_JUPYTER_PORT,
    DEFAULT_REMOTE_ROOT,
    BackpackSource,
    ConnectionConfig,
    EnvironmentConfig,
    RunConfig,
    ValidationIssue,
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConnectionModel(StrictModel):
    target: str
    identity_file: Optional[str] = None
    # `ssh_options` is CLI-only for now: options such as ProxyCommand run local
    # commands, so the web client relies on ~/.ssh/config instead.


class EnvironmentModel(StrictModel):
    env_name: str = DEFAULT_ENV_NAME
    floability_version: str = ""
    conda_executable: str = ""
    reinstall_miniforge: bool = False


class BackpackModel(StrictModel):
    repository: str
    ref: str = ""


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

    def to_config(self) -> RunConfig:
        return RunConfig(
            mode=self.mode,
            connection=ConnectionConfig(
                target=self.connection.target,
                identity_file=self.connection.identity_file or None,
            ),
            backpack=BackpackSource(
                repository=self.backpack.repository, ref=self.backpack.ref
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
        )


class IssueModel(BaseModel):
    field: str
    message: str
    flag: Optional[str] = None

    @classmethod
    def from_issue(cls, issue: ValidationIssue) -> "IssueModel":
        return cls(field=issue.field, message=issue.message, flag=issue.flag)


class ValidationResponse(BaseModel):
    valid: bool
    issues: List[IssueModel]
    command: Optional[str] = Field(
        None, description="Equivalent CLI command when the configuration is valid."
    )


class HealthResponse(BaseModel):
    status: str
    version: str


class FeatureModel(BaseModel):
    available: bool
    milestone: str
    description: str


class DefaultsModel(BaseModel):
    env_name: str
    remote_root: str
    jupyter_port: int


class MetaResponse(BaseModel):
    version: str
    api_version: str
    modes: List[str]
    batch_types: List[str]
    defaults: DefaultsModel
    features: Dict[str, FeatureModel]


class ConnectRequest(StrictModel):
    target: str
    identity_file: Optional[str] = None

    def to_config(self) -> ConnectionConfig:
        return ConnectionConfig(target=self.target, identity_file=self.identity_file or None)


class PromptModel(BaseModel):
    id: str
    kind: str = Field(description="`secret` (hidden text answer) or `confirm` (yes/no).")
    message: str


class ConnectionResponse(BaseModel):
    state: str = Field(description="disconnected, connecting, connected, or failed.")
    target: Optional[str] = None
    identity_file: Optional[str] = None
    remote_user: Optional[str] = None
    remote_host: Optional[str] = None
    prompt: Optional[PromptModel] = None
    error: Optional[str] = None
    connected_at: Optional[float] = None


class PromptAnswer(StrictModel):
    """Answer for an SSH prompt. Never logged or stored."""

    answer: Optional[str] = None
    cancel: bool = False


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


class ErrorBody(BaseModel):
    code: str
    message: str
    issues: List[IssueModel] = Field(default_factory=list)


class ErrorResponse(BaseModel):
    error: ErrorBody
