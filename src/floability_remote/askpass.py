"""Relay OpenSSH password, MFA, and host-key prompts to a client.

With `SSH_ASKPASS_REQUIRE=force` (OpenSSH 8.4+), ssh runs the program named by
`SSH_ASKPASS` for every prompt instead of reading a terminal: passwords, key
passphrases, keyboard-interactive (MFA) questions, and unknown host-key
confirmations. That program is a tiny wrapper around this module's `main`,
which forwards the prompt over a private Unix socket to the `AskPassBroker`
running inside floability-remote and prints the client's answer for ssh.

Answers exist only in memory, are passed to ssh through a pipe, and are never
logged. This module imports only the standard library so the helper starts
quickly.
"""

import json
import os
import re
import secrets
import shlex
import shutil
import socket
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, Optional, Sequence


SOCKET_ENV = "FLOABILITY_REMOTE_ASKPASS_SOCKET"
TOKEN_ENV = "FLOABILITY_REMOTE_ASKPASS_TOKEN"
PROMPT_TIMEOUT_SECONDS = 300
MAX_MESSAGE_BYTES = 64 * 1024

_CONFIRM_PATTERN = re.compile(r"\(yes/no|authenticity of host|continue connecting", re.I)


class PromptKind:
    SECRET = "secret"  # password, passphrase, or MFA response; typed hidden
    CONFIRM = "confirm"  # yes/no question such as trusting a new host key


@dataclass(frozen=True)
class Prompt:
    id: str
    kind: str
    message: str
    created_at: float


def classify(message: str) -> str:
    return PromptKind.CONFIRM if _CONFIRM_PATTERN.search(message) else PromptKind.SECRET


class AskPassBroker:
    """Answer SSH_ASKPASS helper requests through a client callback.

    `on_prompt` is called with the pending `Prompt` when ssh asks a question
    and with `None` once it is answered, cancelled, or times out. Clients reply
    with `answer()`. Use one broker per authentication attempt and `close()` it
    afterwards.
    """

    def __init__(
        self,
        on_prompt: Callable[[Optional[Prompt]], None],
        *,
        timeout: float = PROMPT_TIMEOUT_SECONDS,
        python: str = sys.executable,
    ):
        self._on_prompt = on_prompt
        self._timeout = timeout
        self._token = secrets.token_hex(16)
        self._lock = threading.Lock()
        self._pending: Optional[Prompt] = None
        self._answered = threading.Event()
        self._answer: Optional[str] = None
        self._closed = False

        # mkdtemp creates the directory with mode 0700.
        self._directory = tempfile.mkdtemp(prefix="fr-askpass-")
        self.socket_path = os.path.join(self._directory, "socket")
        self.helper_path = os.path.join(self._directory, "askpass")
        with open(self.helper_path, "w") as helper:
            helper.write(
                "#!/bin/sh\n"
                f"exec {shlex.quote(python)} -m floability_remote.askpass \"$@\"\n"
            )
        os.chmod(self.helper_path, 0o700)

        self._server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server.bind(self.socket_path)
        os.chmod(self.socket_path, 0o600)
        self._server.listen(1)
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def env(self) -> Dict[str, str]:
        """Environment variables that route ssh prompts to this broker."""
        environment = {
            "SSH_ASKPASS": self.helper_path,
            "SSH_ASKPASS_REQUIRE": "force",
            SOCKET_ENV: self.socket_path,
            TOKEN_ENV: self._token,
        }
        # Older OpenSSH releases only use SSH_ASKPASS when DISPLAY is set.
        environment.setdefault("DISPLAY", os.environ.get("DISPLAY", ":0"))
        return environment

    @property
    def pending(self) -> Optional[Prompt]:
        with self._lock:
            return self._pending

    def answer(self, prompt_id: str, answer: Optional[str]) -> bool:
        """Answer the pending prompt; `None` cancels it. False if stale."""
        with self._lock:
            if self._pending is None or self._pending.id != prompt_id:
                return False
            self._answer = answer
            self._answered.set()
            return True

    def cancel(self) -> None:
        with self._lock:
            if self._pending is not None:
                self._answer = None
                self._answered.set()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self.cancel()
        try:
            self._server.close()
        except OSError:
            pass
        self._thread.join(timeout=2)
        shutil.rmtree(self._directory, ignore_errors=True)

    def _serve(self) -> None:
        while True:
            try:
                connection, _ = self._server.accept()
            except OSError:
                return
            with connection:
                try:
                    self._handle(connection)
                except (OSError, ValueError):
                    continue

    def _handle(self, connection: socket.socket) -> None:
        request = json.loads(_read_line(connection))
        if not secrets.compare_digest(str(request.get("token", "")), self._token):
            _send(connection, {"cancel": True})
            return

        message = str(request.get("prompt", "")).strip()
        prompt = Prompt(
            id=secrets.token_hex(8),
            kind=classify(message),
            message=message,
            created_at=time.time(),
        )
        with self._lock:
            if self._closed:
                _send(connection, {"cancel": True})
                return
            self._pending = prompt
            self._answer = None
            self._answered.clear()
        self._on_prompt(prompt)

        answered = self._answered.wait(self._timeout)
        with self._lock:
            answer = self._answer if answered else None
            self._pending = None
            self._answer = None
        self._on_prompt(None)
        _send(connection, {"cancel": True} if answer is None else {"answer": answer})


def _read_line(connection: socket.socket) -> str:
    data = b""
    while not data.endswith(b"\n"):
        chunk = connection.recv(4096)
        if not chunk:
            break
        data += chunk
        if len(data) > MAX_MESSAGE_BYTES:
            raise ValueError("AskPass message is too large.")
    return data.decode("utf-8")


def _send(connection: socket.socket, payload: dict) -> None:
    connection.sendall(json.dumps(payload).encode("utf-8") + b"\n")


def main(argv: Optional[Sequence[str]] = None) -> int:
    """SSH_ASKPASS entry point: print the answer for ssh, or fail to cancel."""
    arguments = sys.argv[1:] if argv is None else list(argv)
    socket_path = os.environ.get(SOCKET_ENV)
    if not socket_path:
        print("floability-remote askpass: no broker is configured.", file=sys.stderr)
        return 1
    request = {"token": os.environ.get(TOKEN_ENV, ""), "prompt": " ".join(arguments)}
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.connect(socket_path)
            _send(connection, request)
            reply = json.loads(_read_line(connection))
    except (OSError, ValueError):
        return 1
    if "answer" not in reply:
        return 1
    sys.stdout.write(f"{reply['answer']}\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
