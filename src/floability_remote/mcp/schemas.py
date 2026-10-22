"""MCP-specific aggregate result models."""

from typing import Optional

from pydantic import BaseModel


class RunStatusSummary(BaseModel):
    """Non-secret summary of the current or most recent daemon run."""

    id: str
    mode: str
    state: str
    target: str
    batch_type: str
    entrypoint: str
    created_at: float
    finished_at: Optional[float] = None
    message: Optional[str] = None


class StatusResult(BaseModel):
    """Overall readiness of the local Floability Remote control plane."""

    client_version: str
    daemon_running: bool
    daemon_version: Optional[str] = None
    api_version: Optional[str] = None
    connection_state: Optional[str] = None
    connection_target: Optional[str] = None
    current_run: Optional[RunStatusSummary] = None
    message: str
