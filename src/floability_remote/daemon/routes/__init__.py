"""Version 1 daemon routes. Routes are thin application adapters."""

from fastapi import APIRouter

from . import backpacks, cluster, connection, files, health, meta, runs, taskvine


api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(meta.router)
api_router.include_router(connection.router)
api_router.include_router(cluster.router)
api_router.include_router(backpacks.router)
api_router.include_router(runs.router)
api_router.include_router(files.router)
api_router.include_router(taskvine.router)
