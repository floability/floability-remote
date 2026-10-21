"""Application operations consumed by every user interface."""

from contextlib import nullcontext
from dataclasses import dataclass


@dataclass(frozen=True)
class ClientContext:
    kind: str = "unknown"
    session_id: str = ""


def operation_context(runtime, client, name: str, *, target: str = "", inputs=None):
    """Audit an operation when it runs inside the long-lived daemon runtime."""
    if runtime is None or client is None:
        return nullcontext()
    return runtime.audit.operation(
        name,
        client=client.kind,
        session_id=client.session_id,
        target=target,
        inputs=inputs,
    )
