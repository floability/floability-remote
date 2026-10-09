from fastapi import APIRouter, Depends, Response

from ...config import connection_issues, normalize_connection
from ...connection import ConnectionConflict, ConnectionSnapshot
from ..errors import ApiError
from ..schemas import (
    ConnectionResponse,
    ConnectRequest,
    IssueModel,
    PromptAnswer,
    PromptModel,
)
from ..services import Services, get_services


router = APIRouter(prefix="/connection", tags=["connection"])


def connection_response(snapshot: ConnectionSnapshot) -> ConnectionResponse:
    prompt = snapshot.prompt
    return ConnectionResponse(
        state=snapshot.state,
        target=snapshot.target,
        identity_file=snapshot.identity_file,
        remote_user=snapshot.remote_user,
        remote_host=snapshot.remote_host,
        prompt=(
            PromptModel(id=prompt.id, kind=prompt.kind, message=prompt.message)
            if prompt
            else None
        ),
        error=snapshot.error,
        connected_at=snapshot.connected_at,
    )


@router.get("", response_model=ConnectionResponse)
def get_connection(services: Services = Depends(get_services)) -> ConnectionResponse:
    return connection_response(services.connections.snapshot())


@router.post("", response_model=ConnectionResponse, status_code=202)
def connect(
    request: ConnectRequest, services: Services = Depends(get_services)
) -> ConnectionResponse:
    """Start authenticating. Poll `GET /connection` for prompts and the result."""
    config = request.to_config()
    issues = connection_issues(config)
    if issues:
        raise ApiError(
            422,
            "invalid_config",
            "The connection settings are not valid.",
            [IssueModel.from_issue(issue) for issue in issues],
        )
    try:
        snapshot = services.connections.connect(normalize_connection(config))
    except ConnectionConflict as error:
        raise ApiError(409, "conflict", str(error)) from error
    return connection_response(snapshot)


@router.delete("", response_model=ConnectionResponse)
def disconnect(services: Services = Depends(get_services)) -> ConnectionResponse:
    if services.runs.has_active_run():
        raise ApiError(409, "run_active", "Stop the active run before disconnecting.")
    return connection_response(services.connections.disconnect())


@router.post("/prompts/{prompt_id}", status_code=204)
def answer_prompt(
    prompt_id: str, body: PromptAnswer, services: Services = Depends(get_services)
) -> Response:
    answer = None if body.cancel else (body.answer or "")
    if not services.connections.answer_prompt(prompt_id, answer):
        raise ApiError(
            404, "prompt_not_found", "This prompt is no longer waiting for an answer."
        )
    return Response(status_code=204)
