"""Local backpack inspection, browsing, cloning, and editor contracts."""

from typing import List, Optional

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
