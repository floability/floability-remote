"""Validated deployment-plan endpoints."""

from fastapi import APIRouter, Depends

from ...application.contracts import (
    DeploymentPlanRequest,
    DeploymentPlanResponse,
    RunResponse,
)
from ...application.operations import ClientContext
from ...application.operations import deployments, plans
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


@router.post("/{plan_id}/start", response_model=RunResponse, status_code=201)
def start_deployment(
    plan_id: str,
    runtime: ApplicationRuntime = Depends(get_runtime),
    client: ClientContext = Depends(get_client),
) -> RunResponse:
    """Start an execute-mode run from a fresh validated plan."""
    return deployments.start_plan(runtime, plan_id, client)
