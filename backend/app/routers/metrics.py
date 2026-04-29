from fastapi import APIRouter

from app.telemetry import telemetry

router = APIRouter(prefix="/v1", tags=["metrics"])


@router.get("/metrics")
def metrics() -> dict:
    return telemetry.snapshot()

