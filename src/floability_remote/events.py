"""Structured progress events consumed by the CLI, web API, and logs."""

import threading
import time
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Dict, Iterable, List, Optional, Protocol


class EventKind:
    """Event kinds. Plain strings keep events JSON-serializable."""

    STEP = "step"  # a numbered top-level stage began
    DETAIL = "detail"  # supporting information about the current stage
    PROGRESS = "progress"  # a recognized Floability milestone
    LOG = "log"  # raw remote output
    READY = "ready"  # an interactive session is accepting connections
    WARNING = "warning"
    CONFIRMATION = "confirmation"  # a client must approve or decline to continue
    CANCELLING = "cancelling"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    ALL = (
        STEP,
        DETAIL,
        PROGRESS,
        LOG,
        READY,
        WARNING,
        CONFIRMATION,
        CANCELLING,
        COMPLETED,
        FAILED,
        CANCELLED,
    )
    TERMINAL = (COMPLETED, FAILED, CANCELLED)


@dataclass(frozen=True)
class Event:
    kind: str
    message: str
    step: Optional[int] = None
    total: Optional[int] = None
    data: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "message": self.message,
            "step": self.step,
            "total": self.total,
            "data": dict(self.data),
            "timestamp": self.timestamp,
        }


class EventSink(Protocol):
    def emit(self, event: Event) -> None:
        ...


class ListSink:
    """Collect events in memory; useful for tests and short-lived consumers."""

    def __init__(self):
        self.events: List[Event] = []
        self._lock = threading.Lock()

    def emit(self, event: Event) -> None:
        with self._lock:
            self.events.append(event)

    def kinds(self) -> List[str]:
        with self._lock:
            return [event.kind for event in self.events]


class CallbackSink:
    def __init__(self, callback: Callable[[Event], None]):
        self._callback = callback

    def emit(self, event: Event) -> None:
        self._callback(event)


class FanoutSink:
    """Deliver each event to several sinks in order."""

    def __init__(self, sinks: Iterable[EventSink]):
        self._sinks = tuple(sinks)

    def emit(self, event: Event) -> None:
        for sink in self._sinks:
            sink.emit(event)


class Emitter:
    """Convenience methods that build events for one operation.

    `logs_visible` tells services that the client already shows raw remote
    output, so error messages need not repeat it.
    """

    def __init__(self, sink: EventSink, *, logs_visible: bool = False):
        self.sink = sink
        self.logs_visible = logs_visible
        self._progress_seen = set()

    def emit(self, kind: str, message: str, **data: Any) -> None:
        self.sink.emit(Event(kind, message, data=data))

    def step(self, current: int, total: int, message: str) -> None:
        self.sink.emit(Event(EventKind.STEP, message, step=current, total=total))

    def detail(self, message: str, **data: Any) -> None:
        self.emit(EventKind.DETAIL, message, **data)

    def progress(self, message: str) -> None:
        """Report a Floability milestone once per operation."""
        if message in self._progress_seen:
            return
        self._progress_seen.add(message)
        self.emit(EventKind.PROGRESS, message)

    def log(self, text: str) -> None:
        """Report raw remote output; `text` keeps its trailing newline."""
        self.emit(EventKind.LOG, text)

    def log_block(self, text: str) -> None:
        """Report captured output that lacks a trailing newline."""
        self.log(text if text.endswith("\n") else f"{text}\n")

    def warning(self, message: str, **data: Any) -> None:
        self.emit(EventKind.WARNING, message, **data)


class Redactor:
    """Replace registered secret values in text."""

    REPLACEMENT = "[REDACTED]"

    def __init__(self):
        self._secrets: List[str] = []
        self._lock = threading.Lock()

    def add(self, secret: Optional[str]) -> None:
        if not secret:
            return
        with self._lock:
            if secret not in self._secrets:
                self._secrets.append(secret)
                self._secrets.sort(key=len, reverse=True)

    def redact(self, text: str) -> str:
        with self._lock:
            secrets = tuple(self._secrets)
        for secret in secrets:
            text = text.replace(secret, self.REPLACEMENT)
        return text


class RedactingSink:
    """Redact registered secrets before forwarding events to another sink.

    `READY` events are forwarded intact: their `url` is the link the user opens
    to reach Jupyter, so only sinks that deliver to the authenticated user should
    receive unredacted events. Put persistent logs behind this sink.
    """

    def __init__(self, sink: EventSink, redactor: Redactor):
        self._sink = sink
        self._redactor = redactor

    def emit(self, event: Event) -> None:
        if event.kind != EventKind.READY:
            redact = self._redactor.redact
            event = replace(
                event,
                message=redact(event.message),
                data={
                    key: redact(value) if isinstance(value, str) else value
                    for key, value in event.data.items()
                },
            )
        self._sink.emit(event)
