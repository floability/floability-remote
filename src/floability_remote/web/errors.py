"""The single JSON error format used by every API response."""

from typing import Iterable, Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from .schemas import ErrorBody, ErrorResponse, IssueModel


STATUS_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    422: "invalid_request",
}


class ApiError(Exception):
    """Raise from a route to return the standard error envelope."""

    def __init__(self, status: int, code: str, message: str, issues=None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.issues = list(issues or ())


def error_response(
    status: int,
    code: str,
    message: str,
    issues: Optional[Iterable[IssueModel]] = None,
) -> JSONResponse:
    body = ErrorResponse(
        error=ErrorBody(code=code, message=message, issues=list(issues or ()))
    )
    return JSONResponse(body.model_dump(), status_code=status)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def api_error(request: Request, error: ApiError) -> JSONResponse:
        return error_response(error.status, error.code, error.message, error.issues)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, error: HTTPException) -> JSONResponse:
        code = STATUS_CODES.get(error.status_code, "error")
        return error_response(error.status_code, code, str(error.detail))

    @app.exception_handler(RequestValidationError)
    async def request_error(request: Request, error: RequestValidationError) -> JSONResponse:
        issues = []
        for item in error.errors():
            location = [str(part) for part in item.get("loc", ())]
            if location and location[0] == "body":
                location = location[1:]
            issues.append(
                IssueModel(field=".".join(location) or "body", message=item.get("msg", ""))
            )
        return error_response(
            422, "invalid_request", "The request body is not valid.", issues
        )
