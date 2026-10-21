"""SSH connection control endpoints."""

from fastapi import APIRouter, Depends, Response

from ...application.operations import ClientContext
from ...application.operations import connections
from ..schemas import (
    ConnectionResponse,
    ConnectRequest,
    PromptAnswer,
)
from ..dependencies import get_client, get_runtime
from ...application.runtime import ApplicationRuntime


router = APIRouter(prefix="/connection", tags=["connection"])


@router.get("", response_model=ConnectionResponse)
def get_connection(
    runtime: ApplicationRuntime = Depends(get_runtime),
    client: ClientContext = Depends(get_client),
) -> ConnectionResponse:
    return connections.get(runtime, client)


@router.post("", response_model=ConnectionResponse, status_code=202)
def connect(
    request: ConnectRequest,
    runtime: ApplicationRuntime = Depends(get_runtime),
    client: ClientContext = Depends(get_client),
) -> ConnectionResponse:
    """Start authenticating. Poll `GET /connection` for prompts and the result."""
    return connections.connect(runtime, request.to_config(), client)


@router.delete("", response_model=ConnectionResponse)
def disconnect(
    runtime: ApplicationRuntime = Depends(get_runtime),
    client: ClientContext = Depends(get_client),
) -> ConnectionResponse:
    return connections.disconnect(runtime, client)


@router.post("/prompts/{prompt_id}", status_code=204)
def answer_prompt(
    prompt_id: str,
    body: PromptAnswer,
    runtime: ApplicationRuntime = Depends(get_runtime),
    client: ClientContext = Depends(get_client),
) -> Response:
    answer = None if body.cancel else (body.answer or "")
    connections.answer_prompt(runtime, prompt_id, answer, client)
    return Response(status_code=204)
