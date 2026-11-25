"""Application service container and FastAPI dependencies."""

from __future__ import annotations

from dataclasses import dataclass

import httpx
from fastapi import Request

from app.clients.abdm import ABDMDrugRegistryClient
from app.clients.cdci import NRCeSCDCIClient
from app.clients.interactions import DrugBankInteractionClient
from app.clients.openfda import OpenFDAClient
from app.clients.rxnorm import RxNormClient
from app.core.cache import SQLiteCache
from app.core.config import Settings
from app.services.interaction_checker import InteractionCheckerService
from app.services.medicine_detail import MedicineDetailService
from app.services.medicine_search import MedicineSearchService


@dataclass
class ServiceContainer:
    """Holds initialized clients, cache, and domain services for the FastAPI app."""

    settings: Settings
    cache: SQLiteCache
    rxnorm: RxNormClient
    openfda: OpenFDAClient
    cdci: NRCeSCDCIClient
    abdm: ABDMDrugRegistryClient
    drugbank: DrugBankInteractionClient
    search_service: MedicineSearchService
    detail_service: MedicineDetailService
    checker_service: InteractionCheckerService


def build_container(
    settings: Settings,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> ServiceContainer:
    """Construct all clients and services from runtime settings."""
    cache = SQLiteCache(settings.resolved_cache_db_path)
    rxnorm = RxNormClient(
        base_url=settings.rxnorm_base_url,
        cache=cache,
        timeout_seconds=settings.http_timeout_seconds,
        cache_ttl_seconds=settings.cache_ttl_seconds,
        transport=transport,
    )
    openfda = OpenFDAClient(
        base_url=settings.openfda_base_url,
        api_key=settings.openfda_api_key,
        cache=cache,
        timeout_seconds=settings.http_timeout_seconds,
        cache_ttl_seconds=settings.cache_ttl_seconds,
        transport=transport,
    )
    cdci = NRCeSCDCIClient(settings.resolved_cdci_data_dir)
    abdm = ABDMDrugRegistryClient(
        client_id=settings.abdm_client_id,
        client_secret=settings.abdm_client_secret,
        gateway_url=settings.abdm_gateway_url,
        registry_url=settings.abdm_drug_registry_url,
        timeout_seconds=settings.http_timeout_seconds,
        transport=transport,
    )
    drugbank = DrugBankInteractionClient(
        api_key=settings.drugbank_api_key,
        base_url=settings.drugbank_base_url,
        cache=cache,
        timeout_seconds=settings.http_timeout_seconds,
        cache_ttl_seconds=settings.cache_ttl_seconds,
        transport=transport,
    )
    search_service = MedicineSearchService(
        rxnorm=rxnorm,
        cdci=cdci,
        abdm=abdm,
        cache=cache,
    )
    detail_service = MedicineDetailService(
        rxnorm=rxnorm,
        openfda=openfda,
        cdci=cdci,
    )
    checker_service = InteractionCheckerService(
        rxnorm=rxnorm,
        openfda=openfda,
        cdci=cdci,
        interaction_provider=drugbank,
        cache=cache,
    )
    return ServiceContainer(
        settings=settings,
        cache=cache,
        rxnorm=rxnorm,
        openfda=openfda,
        cdci=cdci,
        abdm=abdm,
        drugbank=drugbank,
        search_service=search_service,
        detail_service=detail_service,
        checker_service=checker_service,
    )


def get_container(request: Request) -> ServiceContainer:
    """Retrieve ServiceContainer from FastAPI app state."""
    return request.app.state.container
