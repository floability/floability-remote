"""Small authenticated client shared by MCP and future agent adapters."""

import json
import secrets
from typing import Any, Dict, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from ..application.errors import OperationError
from ..application.contracts import (
    ClusterCheckRequest,
    ClusterCheckResponse,
    ConfirmationAnswer,
    BackpackFileListResponse,
    BackpackFileReadRequest,
    BackpackFileResponse,
    BackpackSourceCheckResponse,
    BackpackSourceIdRequest,
    BackpackSourceRequest,
    BackpackSourceResponse,
    ConnectionResponse,
    ConnectRequest,
    DeploymentPlanRequest,
    DeploymentPlanResponse,
    FileListResponse,
    RunFileContentResponse,
    RunFilesDownloadRequest,
    RunFilesDownloadResponse,
    InteractiveSessionResponse,
    InteractiveUrlResponse,
    RunRequest,
    RunEventPageResponse,
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

    def inspect_backpack_source(
        self, request: BackpackSourceRequest
    ) -> BackpackSourceResponse:
        return BackpackSourceResponse.model_validate(
            self.request(
                "POST",
                "/backpacks/sources/inspect",
                request.model_dump(mode="json"),
                timeout=120,
            )
        )

    def check_backpack_source(self, source_id: str) -> BackpackSourceCheckResponse:
        request = BackpackSourceIdRequest(source_id=source_id)
        return BackpackSourceCheckResponse.model_validate(
            self.request(
                "POST", "/backpacks/sources/check", request.model_dump(mode="json")
            )
        )

    def list_backpack_files(self, source_id: str) -> BackpackFileListResponse:
        request = BackpackSourceIdRequest(source_id=source_id)
        return BackpackFileListResponse.model_validate(
            self.request(
                "POST", "/backpacks/sources/files", request.model_dump(mode="json")
            )
        )

    def read_backpack_file(self, source_id: str, path: str) -> BackpackFileResponse:
        request = BackpackFileReadRequest(source_id=source_id, path=path)
        return BackpackFileResponse.model_validate(
            self.request(
                "POST", "/backpacks/sources/read", request.model_dump(mode="json")
            )
        )

    def validate_deployment_plan(
        self, request: DeploymentPlanRequest
    ) -> DeploymentPlanResponse:
        return DeploymentPlanResponse.model_validate(
            self.request(
                "POST", "/plans/validate", request.model_dump(mode="json"), timeout=60
            )
        )

    def start_deployment(self, plan_id: str) -> RunResponse:
        return RunResponse.model_validate(
            self.request("POST", f"/plans/{plan_id}/start", timeout=30)
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

    def get_interactive_session(self, run_id: str) -> InteractiveSessionResponse:
        return InteractiveSessionResponse.model_validate(
            self.request("GET", f"/runs/{run_id}/interactive")
        )

    def get_interactive_url(self, run_id: str) -> InteractiveUrlResponse:
        return InteractiveUrlResponse.model_validate(
            self.request("GET", f"/runs/{run_id}/interactive/url")
        )

    def get_run_events(
        self,
        run_id: str,
        cursor: int = 0,
        limit: int = 50,
        include_logs: bool = False,
    ) -> RunEventPageResponse:
        query = urlencode(
            {
                "cursor": cursor,
                "limit": limit,
                "include_logs": str(include_logs).lower(),
            }
        )
        return RunEventPageResponse.model_validate(
            self.request("GET", f"/runs/{run_id}/event-page?{query}")
        )

    def wait_for_run(
        self,
        run_id: str,
        cursor: int = 0,
        timeout_seconds: float = 10,
        limit: int = 50,
        include_logs: bool = False,
    ) -> RunEventPageResponse:
        query = urlencode(
            {
                "cursor": cursor,
                "timeout_seconds": timeout_seconds,
                "limit": limit,
                "include_logs": str(include_logs).lower(),
            }
        )
        return RunEventPageResponse.model_validate(
            self.request(
                "GET",
                f"/runs/{run_id}/wait?{query}",
                timeout=max(5, min(float(timeout_seconds), 30) + 5),
            )
        )

    def current_run(self) -> RunResponse:
        return RunResponse.model_validate(self.request("GET", "/runs/current"))

    def cancel_run(self, run_id: str) -> RunResponse:
        return RunResponse.model_validate(
            self.request("POST", f"/runs/{run_id}/cancel")
        )

    def answer_run_confirmation(
        self, run_id: str, confirmation_id: str, approved: bool
    ) -> RunResponse:
        body = ConfirmationAnswer(approved=approved)
        self.request(
            "POST",
            f"/runs/{run_id}/confirmations/{confirmation_id}",
            body.model_dump(mode="json"),
        )
        return self.get_run(run_id)

    def list_files(self, run_id: str) -> FileListResponse:
        return FileListResponse.model_validate(
            self.request("GET", f"/runs/{run_id}/files")
        )

    def read_run_file(self, run_id: str, file_id: str) -> RunFileContentResponse:
        try:
            payload = self.request(
                "GET", f"/runs/{run_id}/files/{file_id}/content"
            )
        except OperationError as error:
            # A known run and file are handled by the route with specific error
            # codes.  FastAPI's generic 404 means an older daemon lacks the
            # route, usually because it survived an editable-package update.
            if error.code == "not_found" and error.message == "Not Found":
                raise OperationError(
                    "daemon_update_required",
                    "The running daemon does not provide file preview. Stop it "
                    "with `floability-remote daemon stop` and retry so the "
                    "updated daemon can start.",
                    status=409,
                ) from error
            raise
        return RunFileContentResponse.model_validate(payload)

    def download_run_files(
        self, run_id: str, request: RunFilesDownloadRequest
    ) -> RunFilesDownloadResponse:
        return RunFilesDownloadResponse.model_validate(
            self.request(
                "POST",
                f"/runs/{run_id}/files/download",
                request.model_dump(mode="json"),
                timeout=600,
            )
        )

    def taskvine_status(self, run_id: str) -> TaskVineStatusResponse:
        return TaskVineStatusResponse.model_validate(
            self.request("GET", f"/runs/{run_id}/taskvine")
        )
