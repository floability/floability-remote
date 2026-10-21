"""Start, discover, and stop the one user-scoped local daemon."""

import os
import signal
import subprocess
import sys
import time
import webbrowser
from typing import Optional

from ..application.errors import OperationError
from .client import DaemonClient
from .session import DaemonDescriptor, SessionStore, pid_is_daemon, runtime_directory


START_TIMEOUT = 10.0
STOP_TIMEOUT = 100.0


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def status(store: Optional[SessionStore] = None) -> Optional[DaemonDescriptor]:
    store = store or SessionStore()
    descriptor = store.read()
    if descriptor is None:
        return None
    try:
        DaemonClient(descriptor).meta()
        return descriptor
    except OperationError as error:
        if _pid_alive(descriptor.pid) and pid_is_daemon(descriptor.pid):
            raise OperationError(
                "daemon_unreachable",
                f"Daemon process {descriptor.pid} exists but its API is not responding.",
            ) from error
        store.remove_if_owner(descriptor.pid, descriptor.token)
        return None


def attach(
    client: str = "internal", store: Optional[SessionStore] = None
) -> DaemonClient:
    """Return an authenticated client for the existing healthy daemon."""
    descriptor = status(store)
    if descriptor is None:
        raise OperationError(
            "daemon_not_running", "Floability Remote daemon is not running."
        )
    return DaemonClient(descriptor, client=client)


def start(
    port: Optional[int] = None,
    open_browser: bool = True,
    idle_timeout: float = 3600,
    store: Optional[SessionStore] = None,
) -> DaemonDescriptor:
    store = store or SessionStore()
    existing = status(store)
    if existing is not None:
        if open_browser:
            webbrowser.open(f"http://127.0.0.1:{existing.port}/?token={existing.token}")
        return existing

    directory = runtime_directory()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    log_path = directory / "daemon.log"
    command = [sys.executable, "-m", "floability_remote", "web", "--no-browser"]
    if port is not None:
        command.extend(["--port", str(port)])
    command.extend(["--idle-timeout", str(idle_timeout)])
    with log_path.open("ab") as log:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,
            close_fds=True,
        )

    deadline = time.monotonic() + START_TIMEOUT
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise OperationError(
                "daemon_start_failed",
                f"Daemon exited with status {process.returncode}; see {log_path}.",
            )
        descriptor = store.read()
        if descriptor and descriptor.pid == process.pid:
            try:
                DaemonClient(descriptor).meta()
                if open_browser:
                    webbrowser.open(f"http://127.0.0.1:{descriptor.port}/?token={descriptor.token}")
                return descriptor
            except OperationError:
                pass
        time.sleep(0.05)
    process.terminate()
    raise OperationError("daemon_start_timeout", f"Daemon did not become ready; see {log_path}.")


def stop(store: Optional[SessionStore] = None, force: bool = False) -> None:
    store = store or SessionStore()
    descriptor = status(store)
    if descriptor is None:
        raise OperationError("daemon_not_running", "Floability Remote daemon is not running.")
    client = DaemonClient(descriptor, client="daemon-lifecycle")
    try:
        current = client.current_run()
    except OperationError as error:
        if error.code != "run_not_found":
            raise
    else:
        if current.state == "running" and not force:
            raise OperationError(
                "daemon_busy",
                "A run is active. Re-run `floability-remote daemon stop --yes` "
                "to cancel it and wait for remote cleanup.",
                status=409,
            )
    try:
        os.kill(descriptor.pid, signal.SIGTERM)
    except ProcessLookupError:
        store.remove_if_owner(descriptor.pid, descriptor.token)
        return
    deadline = time.monotonic() + STOP_TIMEOUT
    while time.monotonic() < deadline:
        try:
            os.kill(descriptor.pid, 0)
        except ProcessLookupError:
            store.remove_if_owner(descriptor.pid, descriptor.token)
            return
        time.sleep(0.05)
    raise OperationError("daemon_stop_timeout", "Daemon cleanup did not finish within 100 seconds.")
