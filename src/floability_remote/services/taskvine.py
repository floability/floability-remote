"""Live TaskVine activity for one run: manager, workers, and tasks.

The summary comes from files the run already writes on the cluster:

- TaskVine's transactions log (`vine-run-info/<time>/vine-logs/transactions`
  under the manager's working directory), where the manager appends one line
  for every manager, worker, and task event as it happens; and
- the worker factory's latest status line in `<instance>/logs/vine_factory.stdout`,
  which counts workers submitted to the batch system but not yet connected.

The rules are simple: a worker is connected when its latest line is CONNECTION
and the manager is still running; a task's state is its latest TASK line (DONE
with SUCCESS counts as done, any other DONE result as failed); the manager is
running after START until END.

Not every backpack uses TaskVine, and Floability starts its worker factory for
every run. Only the transactions log shows that the workflow started a TaskVine
manager, so clients present TaskVine activity only when `uses_taskvine` is true.
"""

import json
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from ..domain.errors import RemoteRunError
from ..remote import scripts as remote_scripts
from ..remote.output import marker_values
from ..remote.ssh import SSHSession


@dataclass(frozen=True)
class FactoryStatus:
    """Workers the factory has submitted to the batch system."""

    at: str
    submitted: int
    needed: int
    waiting_connection: int
    requested: int


@dataclass(frozen=True)
class TaskVineEvent:
    time: float
    text: str


@dataclass(frozen=True)
class TaskVineStatus:
    instance_found: bool
    log_found: bool
    manager_state: str = "not_started"  # not_started, running, or ended
    manager_started_at: Optional[float] = None
    manager_ended_at: Optional[float] = None
    workers_connected: int = 0
    workers_seen: int = 0
    tasks_waiting: int = 0
    tasks_running: int = 0
    tasks_done: int = 0
    tasks_failed: int = 0
    updated_at: Optional[float] = None
    factory: Optional[FactoryStatus] = None
    recent: Tuple[TaskVineEvent, ...] = ()

    @property
    def uses_taskvine(self) -> bool:
        """Whether the workflow has started a TaskVine manager in this run."""
        return self.log_found and self.manager_state != "not_started"


def _count(section: Dict[str, Any], key: str) -> int:
    value = section.get(key, 0)
    if not isinstance(value, int) or value < 0:
        raise ValueError(f"invalid count for {key}")
    return value


def _optional_time(value: Any) -> Optional[float]:
    return float(value) if isinstance(value, (int, float)) else None


def parse_status(payload: Dict[str, Any]) -> TaskVineStatus:
    """Convert the remote script's JSON into a TaskVineStatus."""
    factory = payload.get("factory")
    manager = payload.get("manager") or {}
    workers = payload.get("workers") or {}
    tasks = payload.get("tasks") or {}
    state = manager.get("state", "not_started")
    if state not in ("not_started", "running", "ended"):
        raise ValueError("invalid manager state")
    return TaskVineStatus(
        instance_found=bool(payload.get("instance_found")),
        log_found=bool(payload.get("log_found")),
        manager_state=state,
        manager_started_at=_optional_time(manager.get("started_at")),
        manager_ended_at=_optional_time(manager.get("ended_at")),
        workers_connected=_count(workers, "connected"),
        workers_seen=_count(workers, "seen"),
        tasks_waiting=_count(tasks, "waiting"),
        tasks_running=_count(tasks, "running"),
        tasks_done=_count(tasks, "done"),
        tasks_failed=_count(tasks, "failed"),
        updated_at=_optional_time(payload.get("updated_at")),
        factory=(
            FactoryStatus(
                at=str(factory["at"]),
                submitted=_count(factory, "submitted"),
                needed=_count(factory, "needed"),
                waiting_connection=_count(factory, "waiting_connection"),
                requested=_count(factory, "requested"),
            )
            if isinstance(factory, dict)
            else None
        ),
        recent=tuple(
            TaskVineEvent(time=float(item["time"]), text=str(item["text"]))
            for item in payload.get("recent", [])
        ),
    )


class TaskVineService:
    """Read one run's TaskVine activity over an authenticated SSH session."""

    def __init__(self, session: SSHSession):
        self.session = session

    def status(self, run_dir: str) -> TaskVineStatus:
        result = self.session.run_script(remote_scripts.TASKVINE_STATUS, (run_dir,))
        encoded = marker_values(result.stdout or "").get("__FLOABILITY_REMOTE_TASKVINE__")
        if not encoded:
            raise RemoteRunError("The TaskVine status check did not return a result.")
        try:
            return parse_status(json.loads(encoded))
        except (KeyError, TypeError, ValueError) as error:
            raise RemoteRunError("The TaskVine status check returned invalid data.") from error
