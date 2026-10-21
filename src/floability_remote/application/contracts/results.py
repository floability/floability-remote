"""Remote result-file and TaskVine status contracts."""

from typing import List, Optional

from pydantic import BaseModel, Field


class RemoteFileModel(BaseModel):
    id: str
    group: str
    path: str
    size: int
    downloadable: bool
    reason: Optional[str] = None


class FileListResponse(BaseModel):
    run_id: str
    instance_found: bool
    truncated: bool
    files: List[RemoteFileModel]


class FactoryStatusModel(BaseModel):
    at: str
    submitted: int
    needed: int
    waiting_connection: int
    requested: int


class TaskVineEventModel(BaseModel):
    time: float
    text: str


class TaskVineStatusResponse(BaseModel):
    """Live TaskVine activity for one run, read from the run's own logs."""

    run_id: str
    available: bool = Field(description="False until the run has a remote directory.")
    instance_found: bool = False
    log_found: bool = Field(False, description="True once a TaskVine transactions log exists.")
    uses_taskvine: bool = Field(
        False,
        description="True once the workflow has started a TaskVine manager. "
        "Clients show TaskVine activity only then; many backpacks never start one.",
    )
    manager_state: str = "not_started"
    manager_started_at: Optional[float] = None
    manager_ended_at: Optional[float] = None
    workers_connected: int = 0
    workers_seen: int = 0
    tasks_waiting: int = 0
    tasks_running: int = 0
    tasks_done: int = 0
    tasks_failed: int = 0
    updated_at: Optional[float] = None
    factory: Optional[FactoryStatusModel] = None
    recent: List[TaskVineEventModel] = Field(default_factory=list)
    checked_at: float
