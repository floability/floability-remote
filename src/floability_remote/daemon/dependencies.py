"""FastAPI dependencies for the interface-neutral application runtime."""

from fastapi import Request

from ..application.runtime import ApplicationRuntime
from ..application.operations import ClientContext


Services = ApplicationRuntime


def get_runtime(request: Request) -> ApplicationRuntime:
    return request.app.state.runtime


get_services = get_runtime


def get_client(request: Request) -> ClientContext:
    kind = request.headers.get("x-floability-client", "").strip() or "browser"
    session_id = request.headers.get("x-floability-client-session", "").strip()
    return ClientContext(kind=kind[:64], session_id=session_id[:128])
