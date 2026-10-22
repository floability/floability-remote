"""Long-lived state shared by daemon-backed interfaces."""

from dataclasses import dataclass, field
from typing import Optional

from ..deployment.runs import RunManager
from ..remote.connection import ConnectionManager
from .audit import AuditRecorder
from .planning import PlanningStore


@dataclass
class ApplicationRuntime:
    connections: ConnectionManager
    runs: RunManager
    audit: AuditRecorder = field(default_factory=AuditRecorder)
    planning: PlanningStore = field(default_factory=PlanningStore)
    shutting_down: bool = field(default=False)

    @classmethod
    def create(cls, audit: Optional[AuditRecorder] = None) -> "ApplicationRuntime":
        connections = ConnectionManager()
        return cls(
            connections=connections,
            runs=RunManager(connections),
            audit=audit or AuditRecorder(),
        )

    def close(self) -> None:
        """Cancel the active run with cleanup, then close the SSH master."""
        self.shutting_down = True
        self.runs.close()
        self.connections.close()
        self.planning.close()
