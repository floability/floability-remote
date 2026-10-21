"""Private discovery record for the one local Floability Remote daemon."""

import json
import os
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional


def runtime_directory() -> Path:
    configured = os.environ.get("XDG_RUNTIME_DIR")
    if configured:
        return Path(configured).expanduser() / "floability-remote"
    return Path.home() / ".local" / "state" / "floability-remote" / "run"


def descriptor_path() -> Path:
    return runtime_directory() / "daemon.json"


def pid_is_daemon(pid: int) -> bool:
    """Conservatively distinguish our daemon from an unrelated reused PID."""
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return True
    if result.returncode != 0:
        return False
    command = result.stdout.strip()
    return "floability_remote" in command or "floability-remote" in command


@dataclass(frozen=True)
class DaemonDescriptor:
    pid: int
    port: int
    token: str
    started_at: float
    api_version: str = "v1"

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/api/{self.api_version}"

    def public_dict(self):
        return {
            "pid": self.pid,
            "port": self.port,
            "started_at": self.started_at,
            "api_version": self.api_version,
            "base_url": self.base_url,
        }


class SessionStore:
    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path is not None else descriptor_path()

    def read(self) -> Optional[DaemonDescriptor]:
        try:
            stat = self.path.stat()
            if stat.st_mode & 0o077:
                return None
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            return DaemonDescriptor(
                pid=int(payload["pid"]),
                port=int(payload["port"]),
                token=str(payload["token"]),
                started_at=float(payload["started_at"]),
                api_version=str(payload.get("api_version", "v1")),
            )
        except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError, OSError):
            return None

    def write(self, descriptor: DaemonDescriptor) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        handle, temporary = tempfile.mkstemp(prefix="daemon-", dir=str(self.path.parent))
        try:
            os.fchmod(handle, 0o600)
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(asdict(descriptor), stream)
                stream.write("\n")
            os.replace(temporary, self.path)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass

    def remove_if_owner(self, pid: int, token: str) -> None:
        current = self.read()
        if current and current.pid == pid and current.token == token:
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
