"""Stable application errors shared by CLI, HTTP, MCP, and agent adapters."""

from typing import Any, Dict, Iterable, List, Optional


class OperationError(Exception):
    """An expected operation failure with a stable machine-readable code."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status: int = 400,
        issues: Optional[Iterable[Dict[str, Any]]] = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.issues: List[Dict[str, Any]] = list(issues or ())
