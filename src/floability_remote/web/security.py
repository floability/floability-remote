"""Loopback-only access control for the local web server.

Three checks protect the API from other websites open in the same browser:

1. the Host header must name this loopback server, which defeats DNS
   rebinding;
2. cross-origin and cross-site requests are rejected; and
3. API calls must carry the per-process session token, delivered once through
   the URL printed in the terminal and then kept in an HttpOnly cookie.
"""

import hmac
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, Response

from . import API_PREFIX
from .errors import error_response


PUBLIC_API_PATHS = frozenset({f"{API_PREFIX}/health"})
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
SAME_ORIGIN_FETCH_SITES = frozenset({"same-origin", "none"})

CONTENT_SECURITY_POLICY = "; ".join(
    (
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self' data:",
        "connect-src 'self'",
        "base-uri 'none'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    )
)


class SessionGuard:
    def __init__(self, port: int, token: str):
        self.token = token
        self.allowed_hosts = frozenset({f"127.0.0.1:{port}", f"localhost:{port}"})
        self.allowed_origins = frozenset(f"http://{host}" for host in self.allowed_hosts)
        # Cookies are shared across ports on one host; include the port so two
        # servers on the same machine do not overwrite each other's session.
        self.cookie_name = f"floability_remote_session_{port}"

    def token_matches(self, candidate: Optional[str]) -> bool:
        return bool(candidate) and hmac.compare_digest(
            candidate.encode(), self.token.encode()
        )

    def host_allowed(self, request: Request) -> bool:
        return request.headers.get("host", "").lower() in self.allowed_hosts

    def origin_allowed(self, request: Request) -> bool:
        origin = request.headers.get("origin")
        if origin is not None:
            return origin in self.allowed_origins
        fetch_site = request.headers.get("sec-fetch-site")
        if fetch_site is not None and fetch_site not in SAME_ORIGIN_FETCH_SITES:
            return False
        return True

    def authenticated(self, request: Request) -> bool:
        if self.token_matches(request.cookies.get(self.cookie_name)):
            return True
        authorization = request.headers.get("authorization", "")
        scheme, _, credential = authorization.partition(" ")
        return scheme.lower() == "bearer" and self.token_matches(credential.strip())

    def exchange_token(self, token: Optional[str]) -> Optional[Response]:
        """Turn a valid `?token=` into a cookie and drop it from the URL."""
        if not self.token_matches(token):
            return None
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(
            self.cookie_name,
            self.token,
            httponly=True,
            samesite="strict",
            path="/",
        )
        return response


def install_security(app: FastAPI, guard: SessionGuard) -> None:
    @app.middleware("http")
    async def enforce(request: Request, call_next) -> Response:
        if not guard.host_allowed(request):
            return error_response(
                400, "invalid_host", "This server only answers requests for 127.0.0.1."
            )
        if not guard.origin_allowed(request):
            return error_response(
                403, "cross_origin", "Cross-origin requests are not allowed."
            )
        path = request.url.path
        is_api = path == API_PREFIX or path.startswith(f"{API_PREFIX}/")
        if is_api and path not in PUBLIC_API_PATHS and not guard.authenticated(request):
            return error_response(
                401,
                "unauthorized",
                "Open the link printed by `floability-remote web` to sign in.",
            )

        response = await call_next(request)
        response.headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        if is_api:
            response.headers["Cache-Control"] = "no-store"
        return response
