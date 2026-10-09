"""Confirmation and cancellation interfaces between services and clients."""

import threading
from dataclasses import dataclass
from typing import Callable, Optional


@dataclass(frozen=True)
class ConfirmationRequest:
    """A yes/no question that a client must answer before work continues.

    `key` identifies the question so a client can pre-approve it (the CLI's
    `--yes`) or present a tailored control.
    """

    key: str
    message: str


INSTALL_MINIFORGE = "install_miniforge"

Confirm = Callable[[ConfirmationRequest], bool]


def decline(request: ConfirmationRequest) -> bool:
    """Default confirmation policy: never approve without a client answer."""
    return False


class CancelToken:
    """Thread-safe cancellation flag with an optional callback on cancel."""

    def __init__(self):
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._callbacks = []

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self) -> None:
        with self._lock:
            if self._event.is_set():
                return
            self._event.set()
            callbacks = tuple(self._callbacks)
        for callback in callbacks:
            callback()

    def on_cancel(self, callback: Callable[[], None]) -> None:
        """Run `callback` once on cancellation, immediately if already cancelled."""
        with self._lock:
            if not self._event.is_set():
                self._callbacks.append(callback)
                return
        callback()

    def wait(self, timeout: Optional[float] = None) -> bool:
        return self._event.wait(timeout)
