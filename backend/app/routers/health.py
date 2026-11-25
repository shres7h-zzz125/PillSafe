"""Health and data-source availability endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.safety import MEDICAL_DISCLAIMER
from app.dependencies import ServiceContainer, get_container
from app.schemas.medicine import HealthResponse

router = APIRouter(tags=["Health"])


@router.get("/health", response_model=HealthResponse)
async def health_check(
    container: ServiceContainer = Depends(get_container),
) -> HealthResponse:
    """Return service health, SQLite cache metrics, and real data-source availability."""
    rx_version = await container.rxnorm.get_version()
    rx_available = rx_version.get("version") is not None

    sources = [
        container.cdci.status(),
        container.abdm.status(),
        {
            "source_name": container.rxnorm.SOURCE_NAME,
            "configured": True,
            "available": rx_available,
            "dataset_version": rx_version.get("version"),
            "dataset_date": None,
            "message": (
                "RxNorm REST API reachable."
                if rx_available
                else "RxNorm REST API unreachable or offline (DDI endpoint discontinued Jan 2024)."
            ),
        },
        {
            "source_name": container.openfda.SOURCE_NAME,
            "configured": True,
            "available": True,
            "dataset_version": None,
            "dataset_date": None,
            "message": (
                "openFDA Drug Label API configured with API key."
                if container.settings.is_openfda_key_configured
                else "openFDA Drug Label API enabled (no API key; standard 1,000 req/day quota)."
            ),
        },
        container.drugbank.status(),
    ]

    return HealthResponse(
        status="ok",
        app_name=container.settings.app_name,
        version=container.settings.app_version,
        environment=container.settings.app_env,
        cache=container.cache.stats(),
        sources=sources,
        disclaimer=MEDICAL_DISCLAIMER,
    )
