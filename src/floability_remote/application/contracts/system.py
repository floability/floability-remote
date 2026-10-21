"""Daemon metadata, health, and error contracts."""

from typing import Dict, List

from pydantic import BaseModel, Field

from .common import IssueModel


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
    local_tools: Dict[str, bool] = Field(
        default_factory=dict,
        description="Tools found on this computer, such as `vscode` for the `code` command.",
    )


class ErrorBody(BaseModel):
    code: str
    message: str
    issues: List[IssueModel] = Field(default_factory=list)


class ErrorResponse(BaseModel):
    error: ErrorBody
