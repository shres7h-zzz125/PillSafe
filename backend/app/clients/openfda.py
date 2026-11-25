"""Client for the openFDA Drug Labeling API."""

from __future__ import annotations

from typing import Any

import httpx

from app.core.cache import SQLiteCache
from app.core.safety import OPENFDA_DISCLAIMER, build_source_attribution


class OpenFDAClientError(Exception):
    """Raised when openFDA API requests fail unexpectedly."""


class OpenFDAClient:
    """Async client for openFDA /drug/label.json with SQLite TTL caching."""

    SOURCE_NAME = "openFDA Drug Labels"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        cache: SQLiteCache,
        timeout_seconds: float = 10.0,
        cache_ttl_seconds: int = 43200,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key.strip()
        self.cache = cache
        self.timeout_seconds = timeout_seconds
        self.cache_ttl_seconds = cache_ttl_seconds
        self.transport = transport

    @staticmethod
    def _sanitize_term(term: str) -> str:
        return term.replace('"', "").strip()

    async def search_label(
        self,
        *,
        rxcui: str | None = None,
        substance_name: str | None = None,
        generic_name: str | None = None,
        brand_name: str | None = None,
        limit: int = 1,
    ) -> dict[str, Any] | None:
        """Query openFDA drug labels and return normalized label sections + dataset date."""
        clauses: list[str] = []
        if rxcui:
            clauses.append(f'openfda.rxcui:"{self._sanitize_term(rxcui)}"')
        if substance_name:
            clauses.append(f'openfda.substance_name:"{self._sanitize_term(substance_name)}"')
        if generic_name:
            clauses.append(f'openfda.generic_name:"{self._sanitize_term(generic_name)}"')
        if brand_name:
            clauses.append(f'openfda.brand_name:"{self._sanitize_term(brand_name)}"')

        if not clauses:
            return None

        search_expr = " OR ".join(clauses)
        cache_key = f"openfda:label:{search_expr}:{limit}"
        cached = self.cache.get_cached(cache_key)
        if cached is not None:
            return cached["payload"]

        params: dict[str, Any] = {"search": search_expr, "limit": limit}
        if self.api_key:
            params["api_key"] = self.api_key

        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds,
                transport=self.transport,
            ) as client:
                response = await client.get(self.base_url, params=params)
                if response.status_code == 404:
                    self.cache.set_cached(
                        cache_key,
                        source=self.SOURCE_NAME,
                        payload=None,
                        ttl_seconds=self.cache_ttl_seconds,
                    )
                    return None
                if response.status_code == 429:
                    raise OpenFDAClientError("openFDA API rate limit exceeded (HTTP 429).")
                response.raise_for_status()
                raw = response.json()
        except httpx.HTTPError as exc:
            raise OpenFDAClientError(f"openFDA request failed: {exc}") from exc
        except ValueError as exc:
            raise OpenFDAClientError(f"Invalid JSON from openFDA: {exc}") from exc

        results = raw.get("results") or []
        if not results:
            return None

        first = results[0]
        meta = raw.get("meta") or {}
        last_updated = meta.get("last_updated")
        parsed = {
            "last_updated": last_updated,
            "openfda": first.get("openfda") or {},
            "warnings": self._as_excerpt_list(first.get("warnings")),
            "boxed_warning": self._as_excerpt_list(first.get("boxed_warning")),
            "contraindications": self._as_excerpt_list(first.get("contraindications")),
            "dosage_and_administration": self._as_excerpt_list(
                first.get("dosage_and_administration")
            ),
            "drug_interactions_label_text": self._as_excerpt_list(
                first.get("drug_interactions")
            ),
        }
        self.cache.set_cached(
            cache_key,
            source=self.SOURCE_NAME,
            payload=parsed,
            dataset_date=last_updated,
            ttl_seconds=self.cache_ttl_seconds,
        )
        return parsed

    @staticmethod
    def _as_excerpt_list(value: Any, max_chars: int = 1200) -> list[str]:
        if not value:
            return []
        if isinstance(value, str):
            items = [value]
        elif isinstance(value, list):
            items = [str(v) for v in value if v]
        else:
            return []
        trimmed: list[str] = []
        for item in items[:2]:
            text = " ".join(item.split())
            if len(text) > max_chars:
                text = text[:max_chars].rstrip() + "..."
            trimmed.append(text)
        return trimmed

    def attribution(self, dataset_date: str | None = None) -> dict[str, str | None]:
        return build_source_attribution(
            source_name=self.SOURCE_NAME,
            dataset_version=None,
            dataset_date=dataset_date,
            url="https://open.fda.gov/apis/drug/label/",
            notes=OPENFDA_DISCLAIMER,
        )
