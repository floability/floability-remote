"""Contract primitives reused by multiple application features."""

from typing import Optional

from pydantic import BaseModel, ConfigDict

from ...domain.config import ValidationIssue


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IssueModel(BaseModel):
    field: str
    message: str
    flag: Optional[str] = None

    @classmethod
    def from_issue(cls, issue: ValidationIssue) -> "IssueModel":
        return cls(field=issue.field, message=issue.message, flag=issue.flag)
