"""Interaction provider interface and licensed DrugBank API adapter."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import httpx

from app.core.cache import SQLiteCache
from app.core.safety import DRUGBANK_ATTRIBUTION_NOTE, build_source_attribution


class InteractionProvider(ABC):
    """Abstract interface for verified, licensed drug-drug interaction sources."""

    @abstractmethod
    def status(self) -> dict[str, Any]:
        """Return provider configuration and availability status."""

    @abstractmethod
    async def check_pair(
        self,
        *,
        ingredient_a: str,
        ingredient_b: str,
        rxcui_a: str | None = None,
        rxcui_b: str | None = None,
    ) -> dict[str, Any] | None:
        """Return verified interaction data for an ingredient pair, or None if unavailable."""


class DrugBankInteractionClient(InteractionProvider):
    """Adapter for DrugBank API (requires commercial or approved academic license)."""

    SOURCE_NAME = "DrugBank Drug-Drug Interaction API"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        cache: SQLiteCache,
        timeout_seconds: float = 10.0,
        cache_ttl_seconds: int = 43200,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.api_key = api_key.strip()
        self.base_url = base_url.rstrip("/")
        self.cache = cache
        self.timeout_seconds = timeout_seconds
        self.cache_ttl_seconds = cache_ttl_seconds
        self.transport = transport

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    def status(self) -> dict[str, Any]:
        if self.is_configured:
            return {
                "source_name": self.SOURCE_NAME,
                "configured": True,
                "available": True,
                "dataset_version": "v1",
                "dataset_date": None,
                "message": "DrugBank API key configured.",
            }
        return {
            "source_name": self.SOURCE_NAME,
            "configured": False,
            "available": False,
            "dataset_version": None,
            "dataset_date": None,
            "message": (
                "DRUGBANK_API_KEY not configured (requires commercial or approved academic license). "
                "Pairwise interaction checks will return grey 'Could not check'."
            ),
        }

    def attribution(self) -> dict[str, str | None]:
        return build_source_attribution(
            source_name=self.SOURCE_NAME,
            dataset_version="v1",
            dataset_date=None,
            url="https://docs.drugbank.com/",
            notes=DRUGBANK_ATTRIBUTION_NOTE,
        )

    async def check_pair(
        self,
        *,
        ingredient_a: str,
        ingredient_b: str,
        rxcui_a: str | None = None,
        rxcui_b: str | None = None,
    ) -> dict[str, Any] | None:
        """Query DrugBank DDI endpoint when a valid API key is configured."""
        if not self.is_configured:
            return None

        pair_key = "|".join(sorted([ingredient_a.lower().strip(), ingredient_b.lower().strip()]))
        cache_key = f"drugbank:ddi:{pair_key}"
        cached = self.cache.get_cached(cache_key)
        if cached is not None:
            return cached["payload"]

        headers = {"Authorization": f"Bearer {self.api_key}"}
        params: dict[str, Any] = {}
        if rxcui_a and rxcui_b:
            params["rxcui"] = f"{rxcui_a},{rxcui_b}"
        else:
            params["q"] = f"{ingredient_a},{ingredient_b}"

        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds,
                transport=self.transport,
            ) as client:
                response = await client.get(
                    f"{self.base_url}/ddi",
                    params=params,
                    headers=headers,
                )
                if response.status_code in {401, 403, 404}:
                    return None
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError):
            return None

        self.cache.set_cached(
            cache_key,
            source=self.SOURCE_NAME,
            payload=payload,
            ttl_seconds=self.cache_ttl_seconds,
        )
        return payload
