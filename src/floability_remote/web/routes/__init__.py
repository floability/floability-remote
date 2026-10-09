"""Version 1 API routes. Routes translate HTTP to shared services and back."""

from fastapi import APIRouter

from . import connection, files, health, meta, runs


api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(meta.router)
api_router.include_router(connection.router)
api_router.include_router(runs.router)
api_router.include_router(files.router)
