"""Private, redacted audit records for submitted application operations."""

import json
import os
import threading
import time
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any, Dict, Mapping, Optional


MAX_AUDIT_BYTES = 5 * 1024 * 1024
REDACTED = "<redacted>"
SECRET_PARTS = (
    "answer",
    "authorization",
    "cookie",
    "mfa",
    "passphrase",
    "password",
    "secret",
    "token",
)


def default_audit_path() -> Path:
    root = os.environ.get("XDG_STATE_HOME")
    state = Path(root).expanduser() if root else Path.home() / ".local" / "state"
    return state / "floability-remote" / "audit.jsonl"


def _secret_key(key: object) -> bool:
    lowered = str(key).lower()
    return any(part in lowered for part in SECRET_PARTS)


def redact(value: Any) -> Any:
    """Return a JSON-safe copy with known secret-bearing fields removed."""
    if isinstance(value, Mapping):
        return {
            str(key): REDACTED if _secret_key(key) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [redact(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


class AuditRecorder:
    """Append bounded JSONL records with permissions private to the user."""

    def __init__(self, path: Optional[Path] = None, max_bytes: int = MAX_AUDIT_BYTES):
        self.path = Path(path) if path is not None else default_audit_path()
        self.max_bytes = max_bytes
        self._lock = threading.Lock()

    def operation(
        self,
        name: str,
        *,
        client: str,
        session_id: str = "",
        target: str = "",
        inputs: Optional[Mapping[str, Any]] = None,
    ) -> "AuditOperation":
        return AuditOperation(
            self,
            name=name,
            client=client,
            session_id=session_id,
            target=target,
            inputs=inputs,
        )

    def write(self, record: Mapping[str, Any]) -> None:
        payload = json.dumps(redact(record), ensure_ascii=True, sort_keys=True) + "\n"
        with self._lock:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            try:
                os.chmod(self.path.parent, 0o700)
            except OSError:
                pass
            self._rotate_if_needed(len(payload.encode("utf-8")))
            descriptor = os.open(
                str(self.path),
                os.O_APPEND | os.O_CREAT | os.O_WRONLY,
                0o600,
            )
            try:
                os.write(descriptor, payload.encode("utf-8"))
            finally:
                os.close(descriptor)

    def _rotate_if_needed(self, incoming: int) -> None:
        try:
            current = self.path.stat().st_size
        except FileNotFoundError:
            return
        if current + incoming <= self.max_bytes:
            return
        rotated = self.path.with_suffix(".jsonl.1")
        rotated.unlink(missing_ok=True)
        self.path.replace(rotated)


class AuditOperation(AbstractContextManager):
    def __init__(
        self,
        recorder: AuditRecorder,
        *,
        name: str,
        client: str,
        session_id: str,
        target: str,
        inputs: Optional[Mapping[str, Any]],
    ):
        self.recorder = recorder
        self.record: Dict[str, Any] = {
            "timestamp": time.time(),
            "client": client,
            "client_session": session_id,
            "operation": name,
            "target": target,
            "inputs": dict(inputs or {}),
        }
        self.started = time.monotonic()

    def identifiers(self, *, plan_id: str = "", run_id: str = "") -> None:
        if plan_id:
            self.record["plan_id"] = plan_id
        if run_id:
            self.record["run_id"] = run_id

    def outcome(self, value: str, *, code: str = "") -> None:
        self.record["outcome"] = value
        if code:
            self.record["code"] = code

    def __exit__(self, error_type, error, traceback) -> bool:
        if "outcome" not in self.record:
            if error is None:
                self.record["outcome"] = "succeeded"
            else:
                self.record["outcome"] = "failed"
                self.record["code"] = getattr(error, "code", error.__class__.__name__)
        self.record["duration_ms"] = round((time.monotonic() - self.started) * 1000, 3)
        self.recorder.write(self.record)
        return False
