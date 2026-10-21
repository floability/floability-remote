"""Start the local daemon, optionally opening its browser interface."""

import secrets
import os
import threading
import time
import webbrowser
from typing import Optional

import uvicorn

from ..remote.ssh import choose_local_port
from ..application.errors import OperationError
from ..remote.connection import ConnectionState
from .app import WebSettings, create_app
from .client import DaemonClient
from .session import DaemonDescriptor, SessionStore, pid_is_daemon


HOST = "127.0.0.1"


def serve(
    port: Optional[int] = None,
    open_browser: bool = True,
    idle_timeout: Optional[float] = None,
) -> int:
    port = choose_local_port(port)
    settings = WebSettings(port=port, session_token=secrets.token_urlsafe(32))
    app = create_app(settings)
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=HOST,
            port=port,
            log_level="warning",
            # Access logs would record the session token from the sign-in URL.
            access_log=False,
            # Event streams stay open; do not let them delay remote cleanup.
            timeout_graceful_shutdown=5,
        )
    )
    url = f"http://{HOST}:{port}/?token={settings.session_token}"
    descriptor = DaemonDescriptor(
        pid=os.getpid(),
        port=port,
        token=settings.session_token,
        started_at=time.time(),
    )
    store = SessionStore()
    existing = store.read()
    if existing is not None and existing.pid != os.getpid():
        try:
            DaemonClient(existing).meta()
        except OperationError as error:
            try:
                os.kill(existing.pid, 0)
            except ProcessLookupError:
                store.remove_if_owner(existing.pid, existing.token)
                existing = None
            else:
                if not pid_is_daemon(existing.pid):
                    store.remove_if_owner(existing.pid, existing.token)
                    existing = None
            if existing is not None:
                raise OperationError(
                    "daemon_unreachable",
                    f"Daemon process {existing.pid} exists but is not responding.",
                ) from error
        else:
            raise OperationError(
                "daemon_already_running",
                f"Floability Remote daemon is already running at {existing.base_url}.",
            )
    store.write(descriptor)

    print("[web] Floability Remote is running. Open this link to sign in:", flush=True)
    print(url, flush=True)
    print("[web] Press Ctrl+C to stop.", flush=True)
    if open_browser:
        threading.Thread(
            target=_open_when_started, args=(server, url), daemon=True
        ).start()
    if idle_timeout and idle_timeout > 0:
        threading.Thread(
            target=_stop_when_idle,
            args=(server, app, idle_timeout),
            daemon=True,
        ).start()

    try:
        server.run()
        return 0 if server.started else 1
    finally:
        store.remove_if_owner(descriptor.pid, descriptor.token)


def _open_when_started(server: uvicorn.Server, url: str) -> None:
    deadline = time.monotonic() + 10
    while not server.started and not server.should_exit:
        if time.monotonic() > deadline:
            return
        time.sleep(0.05)
    if server.started:
        webbrowser.open(url)


def _stop_when_idle(server: uvicorn.Server, app, idle_timeout: float) -> None:
    """Stop only when no SSH connection or run is alive for the full timeout."""
    while not server.should_exit:
        time.sleep(min(5.0, max(0.1, idle_timeout / 4)))
        runtime = app.state.runtime
        connected = runtime.connections.snapshot().state in (
            ConnectionState.CONNECTING,
            ConnectionState.CONNECTED,
        )
        if connected or runtime.runs.has_active_run():
            app.state.last_activity = time.monotonic()
            continue
        if time.monotonic() - app.state.last_activity >= idle_timeout:
            server.should_exit = True
            return
