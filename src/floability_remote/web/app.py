"""FastAPI application factory for the local web interface."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from .. import __version__
from . import API_PREFIX
from .errors import install_error_handlers
from .routes import api_router
from .security import SessionGuard, install_security
from .services import Services


STATIC_DIR = Path(__file__).parent / "static"


@dataclass(frozen=True)
class WebSettings:
    port: int
    session_token: str


def create_app(settings: WebSettings, services: Optional[Services] = None) -> FastAPI:
    services = services or Services.create()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        # Server shutdown (Ctrl+C): stop remote work cleanly before exiting.
        await asyncio.get_running_loop().run_in_executor(None, services.close)

    app = FastAPI(
        title="Floability Remote",
        version=__version__,
        openapi_url=f"{API_PREFIX}/openapi.json",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    guard = SessionGuard(settings.port, settings.session_token)
    app.state.settings = settings
    app.state.services = services

    install_error_handlers(app)
    app.include_router(api_router, prefix=API_PREFIX)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index(token: Optional[str] = None) -> Response:
        if token is not None:
            exchanged = guard.exchange_token(token)
            if exchanged is not None:
                return exchanged
        return FileResponse(STATIC_DIR / "index.html")

    install_security(app, guard)
    return app
