from fastapi import APIRouter

from ... import __version__
from ...config import (
    DEFAULT_ENV_NAME,
    DEFAULT_JUPYTER_PORT,
    DEFAULT_REMOTE_ROOT,
    SUPPORTED_BATCH_TYPES,
    SUPPORTED_MODES,
)
from ..schemas import DefaultsModel, FeatureModel, MetaResponse


router = APIRouter(tags=["system"])

# Web availability of each capability. Keep docs/web-api.md in sync.
FEATURES = {
    "validate": FeatureModel(
        available=True, milestone="M1", description="Check a run configuration."
    ),
    "connect": FeatureModel(
        available=True,
        milestone="M2",
        description="Open an SSH connection, including password and MFA prompts.",
    ),
    "execute": FeatureModel(
        available=True, milestone="M3", description="Execute a backpack to completion."
    ),
    "cancel": FeatureModel(
        available=True, milestone="M3", description="Stop an active run and clean up."
    ),
    "run": FeatureModel(
        available=True,
        milestone="M4",
        description="Start an interactive backpack and open Jupyter.",
    ),
    "transfer": FeatureModel(
        available=True,
        milestone="M5",
        description="Download individual files retained by completed runs.",
    ),
}


@router.get("/meta", response_model=MetaResponse)
def meta() -> MetaResponse:
    return MetaResponse(
        version=__version__,
        api_version="v1",
        modes=list(SUPPORTED_MODES),
        batch_types=list(SUPPORTED_BATCH_TYPES),
        defaults=DefaultsModel(
            env_name=DEFAULT_ENV_NAME,
            remote_root=DEFAULT_REMOTE_ROOT,
            jupyter_port=DEFAULT_JUPYTER_PORT,
        ),
        features=FEATURES,
    )
