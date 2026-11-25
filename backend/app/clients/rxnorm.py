"""Client for the NLM RxNorm / RxNav REST API (public, no API key required)."""

from __future__ import annotations

from typing import Any

import httpx

from app.core.cache import SQLiteCache
from app.core.safety import RXNORM_ATTRIBUTION_NOTE, build_source_attribution


class RxNormClientError(Exception):
    """Raised when RxNorm API requests fail or return invalid responses."""


class RxNormClient:
    """Async client for NLM RxNav REST API with SQLite TTL caching."""

    SOURCE_NAME = "NLM RxNorm (RxNav REST API)"

    def __init__(
        self,
        *,
        base_url: str,
        cache: SQLiteCache,
        timeout_seconds: float = 10.0,
        cache_ttl_seconds: int = 43200,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.cache = cache
        self.timeout_seconds = timeout_seconds
        self.cache_ttl_seconds = cache_ttl_seconds
        self.transport = transport

    async def _get_json(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        sorted_params = sorted((params or {}).items())
        cache_key = f"rxnorm:{path}:{sorted_params}"
        cached = self.cache.get_cached(cache_key)
        if cached is not None:
            return cached["payload"]

        url = f"{self.base_url}{path}"
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds,
                transport=self.transport,
            ) as client:
                response = await client.get(url, params=params)
                if response.status_code == 429:
                    raise RxNormClientError("RxNorm API rate limit exceeded (HTTP 429).")
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPError as exc:
            raise RxNormClientError(f"RxNorm request failed: {exc}") from exc
        except ValueError as exc:
            raise RxNormClientError(f"Invalid JSON from RxNorm: {exc}") from exc

        self.cache.set_cached(
            cache_key,
            source=self.SOURCE_NAME,
            payload=payload,
            ttl_seconds=self.cache_ttl_seconds,
        )
        return payload

    async def get_version(self) -> dict[str, str | None]:
        """Fetch RxNorm dataset version metadata."""
        try:
            data = await self._get_json("/version.json")
        except RxNormClientError:
            return {"version": None, "api_version": None}
        return {
            "version": data.get("version"),
            "api_version": data.get("apiVersion"),
        }

    def attribution(self, dataset_version: str | None = None) -> dict[str, str | None]:
        return build_source_attribution(
            source_name=self.SOURCE_NAME,
            dataset_version=dataset_version,
            dataset_date=None,
            url="https://lhncbc.nlm.nih.gov/RxNav/APIs/RxNormAPIs.html",
            notes=RXNORM_ATTRIBUTION_NOTE,
        )

    async def approximate_term(self, term: str, max_entries: int = 8) -> list[dict[str, Any]]:
        """Run RxNav approximateTerm search (tolerant of OCR noise and spelling variations)."""
        data = await self._get_json(
            "/approximateTerm.json",
            params={"term": term, "maxEntries": max_entries},
        )
        group = data.get("approximateGroup") or {}
        candidates = group.get("candidate") or []
        if isinstance(candidates, dict):
            return [candidates]
        return list(candidates)

    async def get_drugs(self, name: str) -> list[dict[str, Any]]:
        """Query /drugs.json for drug products matching an ingredient or brand name."""
        data = await self._get_json("/drugs.json", params={"name": name})
        drug_group = data.get("drugGroup") or {}
        concept_groups = drug_group.get("conceptGroup") or []
        results: list[dict[str, Any]] = []
        for group in concept_groups:
            tty = group.get("tty")
            for prop in group.get("conceptProperties") or []:
                item = dict(prop)
                item.setdefault("tty", tty)
                results.append(item)
        return results

    async def find_rxcui_by_name(self, name: str) -> list[str]:
        """Find exact or normalized RxCUI strings for a drug name."""
        data = await self._get_json("/rxcui.json", params={"name": name, "search": 2})
        id_group = data.get("idGroup") or {}
        rxnorm_ids = id_group.get("rxnormId") or []
        if isinstance(rxnorm_ids, str):
            return [rxnorm_ids]
        return [str(item) for item in rxnorm_ids]

    async def get_rxcui_properties(self, rxcui: str) -> dict[str, Any] | None:
        """Fetch core RxNorm properties for a single RxCUI."""
        data = await self._get_json(f"/rxcui/{rxcui}/properties.json")
        props = data.get("properties")
        return dict(props) if isinstance(props, dict) else None

    async def get_all_properties(self, rxcui: str, prop: str = "names codes") -> dict[str, Any]:
        """Fetch categorized names and source codes for an RxCUI."""
        data = await self._get_json(
            f"/rxcui/{rxcui}/allProperties.json",
            params={"prop": prop},
        )
        return data.get("propConceptGroup") or {}

    async def get_ingredients(self, rxcui: str) -> list[dict[str, str]]:
        """Resolve active ingredients (IN, MIN, PIN) related to an RxCUI."""
        data = await self._get_json(
            f"/rxcui/{rxcui}/related.json",
            params={"tty": "IN+MIN+PIN"},
        )
        related_group = data.get("relatedGroup") or {}
        concept_groups = related_group.get("conceptGroup") or []
        ingredients: list[dict[str, str]] = []
        seen: set[str] = set()
        for group in concept_groups:
            tty = group.get("tty") or ""
            for prop in group.get("conceptProperties") or []:
                ing_rxcui = str(prop.get("rxcui") or "")
                ing_name = str(prop.get("name") or "").strip()
                if not ing_name:
                    continue
                dedupe_key = ing_rxcui or ing_name.lower()
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                ingredients.append(
                    {
                        "rxcui": ing_rxcui,
                        "name": ing_name,
                        "tty": tty,
                    }
                )
        return ingredients
