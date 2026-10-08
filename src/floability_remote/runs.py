"""Background runs for clients that cannot block on a workflow, such as the web API.

`RunManager` starts `RemoteWorkflow` on a worker thread using the session from
`ConnectionManager`, records every (redacted) event so clients can replay and
follow them, relays confirmation requests, and cancels with the workflow's
normal remote cleanup. One run may be active at a time.
"""

import secrets
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from .config import RunConfig
from .connection import ConnectionManager
from .errors import RemoteRunError
from .events import Event, EventKind, RedactingSink, Redactor
from .interaction import CancelToken, ConfirmationRequest
from .workflow import RemoteWorkflow


CONFIRMATION_TIMEOUT_SECONDS = 15 * 60


class RunState:
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    TERMINAL = (COMPLETED, FAILED, CANCELLED)


class RunConflict(RemoteRunError):
    """A run request conflicts with the active run or connection."""


class RunNotFound(RemoteRunError):
    pass


@dataclass(frozen=True)
class PendingConfirmation:
    id: str
    key: str
    message: str


class Run:
    """One background workflow and the events it has emitted so far."""

    def __init__(self, config: RunConfig):
        self.id = time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(4)
        self.config = config
        self.created_at = time.time()
        self.finished_at: Optional[float] = None
        self.state = RunState.RUNNING
        self.message: Optional[str] = None
        self.result: Dict[str, str] = {}
        self.jupyter_url: Optional[str] = None
        self.cancel_token = CancelToken()
        self.cancel_requested = False
        self._events: List[Event] = []
        self._changed = threading.Condition()
        self._confirmation: Optional[PendingConfirmation] = None
        self._confirmation_answer: Optional[bool] = None
        self._confirmation_ready = threading.Event()

    # EventSink -------------------------------------------------------------

    def emit(self, event: Event) -> None:
        with self._changed:
            self._events.append(event)
            if event.kind == EventKind.READY:
                self.jupyter_url = event.data.get("url")
            if event.kind in EventKind.TERMINAL:
                # The tunnel is closed once a run ends; its link no longer works.
                self.jupyter_url = None
                self.state = event.kind
                self.message = event.message
                self.finished_at = event.timestamp
                self.result = {
                    key: str(value) for key, value in event.data.items() if value is not None
                }
            self._changed.notify_all()

    # Reading ---------------------------------------------------------------

    @property
    def finished(self) -> bool:
        return self.state in RunState.TERMINAL

    @property
    def event_count(self) -> int:
        with self._changed:
            return len(self._events)

    @property
    def confirmation(self) -> Optional[PendingConfirmation]:
        with self._changed:
            return self._confirmation

    def events_after(self, index: int) -> List[Event]:
        """Events with position >= `index` (positions start at 0)."""
        with self._changed:
            return list(self._events[max(index, 0):])

    def wait_for_events(self, index: int, timeout: float) -> bool:
        """Block until more than `index` events exist or the run finishes."""
        with self._changed:
            return self._changed.wait_for(
                lambda: len(self._events) > index or self.finished, timeout
            )

    # Confirmation ----------------------------------------------------------

    def confirm(self, request: ConfirmationRequest) -> bool:
        """`interaction.Confirm` implementation: wait for a client answer."""
        pending = PendingConfirmation(secrets.token_hex(6), request.key, request.message)
        with self._changed:
            self._confirmation = pending
            self._confirmation_answer = None
            self._confirmation_ready.clear()
        self.emit(
            Event(
                EventKind.CONFIRMATION,
                request.message,
                data={"id": pending.id, "key": request.key},
            )
        )
        answered = self._confirmation_ready.wait(CONFIRMATION_TIMEOUT_SECONDS)
        with self._changed:
            approved = bool(answered and self._confirmation_answer)
            self._confirmation = None
        self.emit(
            Event(
                EventKind.DETAIL,
                "Approved." if approved else "Declined.",
                data={"confirmation_id": pending.id, "approved": approved},
            )
        )
        return approved

    def answer_confirmation(self, confirmation_id: str, approved: bool) -> bool:
        with self._changed:
            if self._confirmation is None or self._confirmation.id != confirmation_id:
                return False
            self._confirmation_answer = approved
        self._confirmation_ready.set()
        return True

    def decline_pending(self) -> None:
        with self._changed:
            pending = self._confirmation
            self._confirmation_answer = False
        if pending is not None:
            self._confirmation_ready.set()


class RunManager:
    def __init__(
        self,
        connections: ConnectionManager,
        workflow_factory: Callable[..., RemoteWorkflow] = RemoteWorkflow,
    ):
        self._connections = connections
        self._workflow_factory = workflow_factory
        self._lock = threading.Lock()
        self._runs: Dict[str, Run] = {}
        self._latest: Optional[Run] = None
        self._threads: Dict[str, threading.Thread] = {}

    def start(self, config: RunConfig) -> Run:
        """Start `config` on the open connection. `config` must be validated."""
        with self._lock:
            if self._latest is not None and not self._latest.finished:
                raise RunConflict("Another run is still active.")
            session = self._connections.session_for(config.connection.target)
            run = Run(config)
            redactor = Redactor()
            workflow = self._workflow_factory(
                config,
                RedactingSink(run, redactor),
                confirm=run.confirm,
                cancel_token=run.cancel_token,
                redactor=redactor,
                session=session,
            )
            thread = threading.Thread(
                target=self._execute, args=(workflow,), name=f"run-{run.id}", daemon=True
            )
            self._runs[run.id] = run
            self._latest = run
            self._threads[run.id] = thread
            thread.start()
            return run

    def get(self, run_id: str) -> Run:
        with self._lock:
            run = self._runs.get(run_id)
        if run is None:
            raise RunNotFound(f"Run {run_id} was not found.")
        return run

    def latest(self) -> Optional[Run]:
        with self._lock:
            return self._latest

    def has_active_run(self) -> bool:
        latest = self.latest()
        return latest is not None and not latest.finished

    def cancel(self, run_id: str) -> Run:
        """Request cancellation; remote cleanup continues in the background."""
        run = self.get(run_id)
        if run.finished or run.cancel_requested:
            return run
        run.cancel_requested = True

        def cancel_and_release() -> None:
            # Mark the token first so a declined confirmation is reported as a
            # cancellation rather than a failure.
            run.cancel_token.cancel()
            run.decline_pending()

        # CancelToken callbacks run the remote SIGINT/SIGTERM cleanup, which can
        # take up to a minute, so never run them on the caller's thread.
        threading.Thread(target=cancel_and_release, daemon=True).start()
        return run

    def close(self, timeout: float = 90) -> None:
        """Cancel the active run and wait for its cleanup to finish."""
        latest = self.latest()
        if latest is None or latest.finished:
            return
        self.cancel(latest.id)
        with self._lock:
            thread = self._threads.get(latest.id)
        if thread is not None:
            thread.join(timeout)

    @staticmethod
    def _execute(workflow: RemoteWorkflow) -> None:
        try:
            workflow.start()
        except Exception:
            # The workflow has already emitted a `failed` event with the reason.
            pass
