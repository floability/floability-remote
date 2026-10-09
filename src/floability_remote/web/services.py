"""Shared service instances for one web server process."""

from dataclasses import dataclass, field

from fastapi import Request

from ..connection import ConnectionManager
from ..runs import RunManager


@dataclass
class Services:
    connections: ConnectionManager
    runs: RunManager
    shutting_down: bool = field(default=False)

    @classmethod
    def create(cls) -> "Services":
        connections = ConnectionManager()
        return cls(connections=connections, runs=RunManager(connections))

    def close(self) -> None:
        """Cancel the active run with remote cleanup, then disconnect."""
        self.shutting_down = True
        if self.runs.has_active_run():
            print(
                "[web] Stopping: cancelling the active run and cleaning up remotely...",
                flush=True,
            )
        self.runs.close()
        self.connections.close()


def get_services(request: Request) -> Services:
    return request.app.state.services
