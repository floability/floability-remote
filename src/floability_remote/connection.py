"""A long-lived SSH connection that clients open, reuse, and close explicitly.

The CLI opens a connection per command inside `RemoteWorkflow`. Clients that
outlive one command, such as the web interface, use `ConnectionManager`: it
authenticates once through AskPass (so password, MFA, and host-key prompts can
be answered by the client), keeps the OpenSSH control master alive, and hands
the session to workflows.
"""

import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

from . import remote_scripts
from .askpass import AskPassBroker, Prompt
from .config import ConnectionConfig
from .errors import RemoteRunError
from .output import marker_values
from .ssh import SSHSession


# Idle lifetime of the control master once no command is using it.
CONTROL_PERSIST = "1h"


class ConnectionState:
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    FAILED = "failed"


class ConnectionConflict(RemoteRunError):
    """A connection request conflicts with the current connection state."""


@dataclass(frozen=True)
class ConnectionSnapshot:
    state: str
    target: Optional[str] = None
    identity_file: Optional[str] = None
    remote_user: Optional[str] = None
    remote_host: Optional[str] = None
    prompt: Optional[Prompt] = None
    error: Optional[str] = None
    connected_at: Optional[float] = None


class ConnectionManager:
    """Own at most one authenticated SSH connection at a time."""

    def __init__(
        self,
        session_factory: Callable[..., SSHSession] = SSHSession,
        broker_factory: Callable[..., AskPassBroker] = AskPassBroker,
    ):
        self._session_factory = session_factory
        self._broker_factory = broker_factory
        self._lock = threading.Lock()
        self._state = ConnectionState.DISCONNECTED
        self._config: Optional[ConnectionConfig] = None
        self._session: Optional[SSHSession] = None
        self._broker: Optional[AskPassBroker] = None
        self._prompt: Optional[Prompt] = None
        self._error: Optional[str] = None
        self._remote_user: Optional[str] = None
        self._remote_host: Optional[str] = None
        self._connected_at: Optional[float] = None
        self._thread: Optional[threading.Thread] = None

    def snapshot(self) -> ConnectionSnapshot:
        with self._lock:
            if self._state == ConnectionState.CONNECTED and not self._session_alive():
                self._mark_lost()
            return ConnectionSnapshot(
                state=self._state,
                target=self._config.target if self._config else None,
                identity_file=self._config.identity_file if self._config else None,
                remote_user=self._remote_user,
                remote_host=self._remote_host,
                prompt=self._prompt,
                error=self._error,
                connected_at=self._connected_at,
            )

    def connect(self, config: ConnectionConfig) -> ConnectionSnapshot:
        """Start authenticating in the background; poll `snapshot()` for progress."""
        with self._lock:
            if self._state in (ConnectionState.CONNECTING, ConnectionState.CONNECTED):
                raise ConnectionConflict(
                    f"Already {self._state} to {self._config.target}; disconnect first."
                )
            self._state = ConnectionState.CONNECTING
            self._config = config
            self._error = None
            self._prompt = None
            self._remote_user = self._remote_host = self._connected_at = None
            self._thread = threading.Thread(
                target=self._authenticate, args=(config,), daemon=True
            )
            self._thread.start()
        return self.snapshot()

    def answer_prompt(self, prompt_id: str, answer: Optional[str]) -> bool:
        """Answer the pending SSH prompt; `None` cancels it."""
        with self._lock:
            broker = self._broker
        return broker is not None and broker.answer(prompt_id, answer)

    def session_for(self, target: str) -> SSHSession:
        """Return the live session for `target` or raise ConnectionConflict."""
        with self._lock:
            if self._state != ConnectionState.CONNECTED or self._session is None:
                raise ConnectionConflict("Connect to the login node before starting a run.")
            if self._config is None or self._config.target != target:
                raise ConnectionConflict(
                    f"The open connection is to {self._config.target if self._config else 'another host'}, "
                    f"not {target}."
                )
            if not self._session_alive():
                self._mark_lost()
                raise ConnectionConflict(self._error or "The SSH connection was lost.")
            return self._session

    def disconnect(self) -> ConnectionSnapshot:
        with self._lock:
            broker, thread = self._broker, self._thread
        if broker is not None:
            broker.cancel()
        if thread is not None and thread is not threading.current_thread():
            # Authentication fails promptly once its pending prompt is cancelled.
            thread.join(timeout=30)
        with self._lock:
            session = self._session
            self._session = None
            self._state = ConnectionState.DISCONNECTED
            self._prompt = None
            self._error = None
            self._remote_user = self._remote_host = self._connected_at = None
        if session is not None:
            session.close()
        return self.snapshot()

    def close(self) -> None:
        self.disconnect()

    def _authenticate(self, config: ConnectionConfig) -> None:
        broker = self._broker_factory(self._set_prompt)
        with self._lock:
            self._broker = broker
        session = None
        try:
            session = self._session_factory(
                config.target,
                identity_file=config.identity_file,
                ssh_options=config.ssh_options,
                askpass_env=broker.env(),
                control_persist=CONTROL_PERSIST,
            )
            session.start()
            result = session.run_script(remote_scripts.IDENTIFY)
            values = marker_values(result.stdout or "")
        except RemoteRunError as error:
            if session is not None:
                session.close()
            self._finish(error=str(error))
            return
        except Exception as error:  # pragma: no cover - unexpected local failure
            if session is not None:
                session.close()
            self._finish(error=f"Could not connect: {error}")
            return
        finally:
            broker.close()
            with self._lock:
                self._broker = None
                self._prompt = None

        self._finish(
            session=session,
            user=values.get("__FLOABILITY_REMOTE_USER__"),
            host=values.get("__FLOABILITY_REMOTE_HOST__"),
        )

    def _finish(self, *, session=None, user=None, host=None, error=None) -> None:
        with self._lock:
            if self._state != ConnectionState.CONNECTING:
                # Disconnected while authenticating; discard the result.
                if session is not None:
                    session.close()
                return
            if error is not None:
                self._state = ConnectionState.FAILED
                self._error = error
                return
            self._session = session
            self._state = ConnectionState.CONNECTED
            self._remote_user = user
            self._remote_host = host
            self._connected_at = time.time()

    def _set_prompt(self, prompt: Optional[Prompt]) -> None:
        with self._lock:
            self._prompt = prompt

    def _session_alive(self) -> bool:
        return self._session is not None and self._session.is_alive()

    def _mark_lost(self) -> None:
        self._state = ConnectionState.FAILED
        self._error = "The SSH connection was lost. Connect again."
        self._session = None
        self._connected_at = None
