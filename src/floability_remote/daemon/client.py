"""Small authenticated client shared by MCP and future agent adapters."""

import json
import secrets
from typing import Any, Dict, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ..application.errors import OperationError
from ..application.contracts import (
    ClusterCheckRequest,
    ClusterCheckResponse,
    ConnectionResponse,
    ConnectRequest,
    FileListResponse,
    RunRequest,
    RunResponse,
    TaskVineStatusResponse,
    ValidationResponse,
)
from .session import DaemonDescriptor, SessionStore


class DaemonClient:
    def __init__(
        self,
        descriptor: DaemonDescriptor,
        client: str = "internal",
        session_id: str = "",
    ):
        self.descriptor = descriptor
        self.client = client
        self.session_id = session_id or secrets.token_hex(8)

    @classmethod
    def discover(
        cls,
        client: str = "internal",
        session_id: str = "",
        store: Optional[SessionStore] = None,
    ):
        descriptor = (store or SessionStore()).read()
        if descriptor is None:
            raise OperationError(
                "daemon_not_running", "Floability Remote daemon is not running."
            )
        return cls(descriptor, client=client, session_id=session_id)

    def request(
        self,
        method: str,
        path: str,
        payload: Optional[Dict[str, Any]] = None,
        timeout: float = 5,
    ) -> Dict[str, Any]:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = Request(
            f"{self.descriptor.base_url}{path}",
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self.descriptor.token}",
                "Content-Type": "application/json",
                "X-Floability-Client": self.client,
                "X-Floability-Client-Session": self.session_id,
            },
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                body = response.read()
        except HTTPError as error:
            try:
                detail = json.loads(error.read()).get("error", {})
            except (ValueError, AttributeError):
                detail = {}
            raise OperationError(
                detail.get("code", "daemon_request_failed"),
                detail.get("message", str(error)),
                status=error.code,
                issues=detail.get("issues", []),
            ) from error
        except (URLError, TimeoutError, OSError) as error:
            raise OperationError(
                "daemon_unreachable", f"Cannot reach Floability Remote daemon: {error}"
            ) from error
        return json.loads(body) if body else {}

    def health(self) -> Dict[str, Any]:
        return self.request("GET", "/health")

    def meta(self) -> Dict[str, Any]:
        return self.request("GET", "/meta")

    def get_connection(self) -> ConnectionResponse:
        return ConnectionResponse.model_validate(self.request("GET", "/connection"))

    def connect(self, request: ConnectRequest) -> ConnectionResponse:
        return ConnectionResponse.model_validate(
            self.request("POST", "/connection", request.model_dump(mode="json"))
        )

    def disconnect(self) -> ConnectionResponse:
        return ConnectionResponse.model_validate(self.request("DELETE", "/connection"))

    def answer_prompt(self, prompt_id: str, answer: Optional[str]) -> None:
        payload = {"cancel": answer is None, "answer": answer}
        self.request("POST", f"/connection/prompts/{prompt_id}", payload)

    def check_cluster(self, request: ClusterCheckRequest) -> ClusterCheckResponse:
        return ClusterCheckResponse.model_validate(
            self.request(
                "POST", "/cluster/check", request.model_dump(mode="json"), timeout=30
            )
        )

    def validate_run(self, request: RunRequest) -> ValidationResponse:
        return ValidationResponse.model_validate(
            self.request("POST", "/runs/validate", request.model_dump(mode="json"))
        )

    def start_run(self, request: RunRequest) -> RunResponse:
        return RunResponse.model_validate(
            self.request("POST", "/runs", request.model_dump(mode="json"))
        )

    def get_run(self, run_id: str) -> RunResponse:
        return RunResponse.model_validate(self.request("GET", f"/runs/{run_id}"))

    def current_run(self) -> RunResponse:
        return RunResponse.model_validate(self.request("GET", "/runs/current"))

    def cancel_run(self, run_id: str) -> RunResponse:
        return RunResponse.model_validate(
            self.request("POST", f"/runs/{run_id}/cancel")
        )

    def list_files(self, run_id: str) -> FileListResponse:
        return FileListResponse.model_validate(
            self.request("GET", f"/runs/{run_id}/files")
        )

    def taskvine_status(self, run_id: str) -> TaskVineStatusResponse:
        return TaskVineStatusResponse.model_validate(
            self.request("GET", f"/runs/{run_id}/taskvine")
        )
