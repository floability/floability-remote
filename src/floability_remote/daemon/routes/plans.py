"""Validated deployment-plan endpoints."""

from fastapi import APIRouter, Depends

from ...application.contracts import DeploymentPlanRequest, DeploymentPlanResponse
from ...application.operations import ClientContext
from ...application.operations import plans
from ...application.runtime import ApplicationRuntime
from ..dependencies import get_client, get_runtime


router = APIRouter(prefix="/plans", tags=["plans"])


@router.post("/validate", response_model=DeploymentPlanResponse)
def validate_deployment(
    body: DeploymentPlanRequest,
    runtime: ApplicationRuntime = Depends(get_runtime),
    client: ClientContext = Depends(get_client),
) -> DeploymentPlanResponse:
    return plans.validate_plan(runtime, body, client)
