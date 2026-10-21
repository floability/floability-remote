"""Connection operations over the application runtime."""

from typing import Optional

from ...domain.config import ConnectionConfig, connection_issues, normalize_connection
from ...remote.connection import ConnectionConflict, ConnectionSnapshot
from ..errors import OperationError
from ..contracts import ConnectionResponse, IssueModel, PromptModel
from ..runtime import ApplicationRuntime
from . import ClientContext


def response(snapshot: ConnectionSnapshot) -> ConnectionResponse:
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


def get(runtime: ApplicationRuntime, client: ClientContext) -> ConnectionResponse:
    with runtime.audit.operation("connection.get", client=client.kind, session_id=client.session_id):
        return response(runtime.connections.snapshot())


def connect(
    runtime: ApplicationRuntime,
    config: ConnectionConfig,
    client: ClientContext,
) -> ConnectionResponse:
    issues = connection_issues(config)
    if issues:
        raise OperationError(
            "invalid_config",
            "The connection settings are not valid.",
            status=422,
            issues=[IssueModel.from_issue(issue).model_dump() for issue in issues],
        )
    normalized = normalize_connection(config)
    with runtime.audit.operation(
        "connection.connect",
        client=client.kind,
        session_id=client.session_id,
        target=normalized.target,
        inputs={"identity_file": bool(normalized.identity_file)},
    ):
        try:
            return response(runtime.connections.connect(normalized))
        except ConnectionConflict as error:
            raise OperationError("conflict", str(error), status=409) from error


def disconnect(runtime: ApplicationRuntime, client: ClientContext) -> ConnectionResponse:
    if runtime.runs.has_active_run():
        raise OperationError(
            "run_active", "Stop the active run before disconnecting.", status=409
        )
    snapshot = runtime.connections.snapshot()
    with runtime.audit.operation(
        "connection.disconnect",
        client=client.kind,
        session_id=client.session_id,
        target=snapshot.target or "",
    ):
        return response(runtime.connections.disconnect())


def answer_prompt(
    runtime: ApplicationRuntime,
    prompt_id: str,
    answer: Optional[str],
    client: ClientContext,
) -> None:
    snapshot = runtime.connections.snapshot()
    with runtime.audit.operation(
        "connection.answer_prompt",
        client=client.kind,
        session_id=client.session_id,
        target=snapshot.target or "",
        inputs={"prompt_id": prompt_id, "answer": answer},
    ):
        if not runtime.connections.answer_prompt(prompt_id, answer):
            raise OperationError(
                "prompt_not_found",
                "This prompt is no longer waiting for an answer.",
                status=404,
            )
