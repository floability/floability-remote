"""Local backpack inspection, browsing, cloning, and editor contracts."""

from typing import List, Literal, Optional

from pydantic import BaseModel, Field

from .common import StrictModel


class BackpackPathRequest(StrictModel):
    path: str


class BackpackInspectionResponse(BaseModel):
    path: str
    name: str
    source_type: str
    file_count: int
    total_bytes: int
    workflow_found: bool
    valid: bool
    problems: List[str]
    workflow_files: List[str] = Field(default_factory=list)


class BackpackBrowseRequest(StrictModel):
    path: str = ""


class DirectoryModel(BaseModel):
    name: str
    path: str
    is_backpack: bool


class BackpackBrowseResponse(BaseModel):
    path: str
    parent: Optional[str] = None
    is_backpack: bool
    directories: List[DirectoryModel]
    truncated: bool


class BackpackCloneRequest(StrictModel):
    repository: str
    ref: str = ""
    destination: str = ""


class BackpackProvenanceModel(BaseModel):
    local_path: str
    original_repository: str = ""
    original_ref: str = ""
    commit: str = ""
    uncommitted_changes: int = 0


class BackpackCloneResponse(BaseModel):
    """A new editable folder cloned from a Git repository."""

    path: str
    inspection: BackpackInspectionResponse
    provenance: BackpackProvenanceModel


class BackpackOpenRequest(StrictModel):
    path: str
    editor: str = "folder"


class BackpackSourceRequest(StrictModel):
    source_type: Literal["git", "folder", "zip"] = "git"
    repository: str = ""
    ref: str = ""
    local_path: str = ""


class BackpackSourceResponse(BaseModel):
    source_id: str
    source_type: str
    location: str
    requested_ref: str = ""
    resolved_ref: str = ""
    commit: str = ""
    inspection: BackpackInspectionResponse
    entrypoints: List[str] = Field(default_factory=list)
    created_at: float


class BackpackSourceCheckResponse(BaseModel):
    source_id: str
    fresh: bool
    message: str


class BackpackSourceIdRequest(StrictModel):
    source_id: str


class BackpackFileModel(BaseModel):
    path: str
    size: int


class BackpackFileListResponse(BaseModel):
    source_id: str
    files: List[BackpackFileModel]


class BackpackFileReadRequest(StrictModel):
    source_id: str
    path: str


class BackpackFileResponse(BaseModel):
    source_id: str
    path: str
    content: str
