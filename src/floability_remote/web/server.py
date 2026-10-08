"""Start the local web server for `floability-remote web`."""

import secrets
import threading
import time
import webbrowser
from typing import Optional

import uvicorn

from ..ssh import choose_local_port
from .app import WebSettings, create_app


HOST = "127.0.0.1"


def serve(port: Optional[int] = None, open_browser: bool = True) -> int:
    port = choose_local_port(port)
    settings = WebSettings(port=port, session_token=secrets.token_urlsafe(32))
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(settings),
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

    print("[web] Floability Remote is running. Open this link to sign in:", flush=True)
    print(url, flush=True)
    print("[web] Press Ctrl+C to stop.", flush=True)
    if open_browser:
        threading.Thread(
            target=_open_when_started, args=(server, url), daemon=True
        ).start()

    server.run()
    return 0 if server.started else 1


def _open_when_started(server: uvicorn.Server, url: str) -> None:
    deadline = time.monotonic() + 10
    while not server.started and not server.should_exit:
        if time.monotonic() > deadline:
            return
        time.sleep(0.05)
    if server.started:
        webbrowser.open(url)
