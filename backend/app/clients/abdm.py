"""Client for the ABDM Drug Registry API (requires ABDM sandbox/production credentials)."""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx

from app.core.safety import ABDM_ATTRIBUTION_NOTE, build_source_attribution


class ABDMDrugRegistryError(Exception):
    """Raised when ABDM Drug Registry calls fail."""


class ABDMDrugRegistryClient:
    """Async client for ABDM Gateway session authentication and Drug Registry /dr/v3."""

    SOURCE_NAME = "ABDM Drug Registry"

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        gateway_url: str,
        registry_url: str,
        timeout_seconds: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.client_id = client_id.strip()
        self.client_secret = client_secret.strip()
        self.gateway_url = gateway_url.rstrip("/")
        self.registry_url = registry_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.transport = transport
        self._access_token: str | None = None
        self._token_expires_at: float = 0.0

    @property
    def is_configured(self) -> bool:
        return bool(self.client_id and self.client_secret)

    def status(self) -> dict[str, Any]:
        if self.is_configured:
            return {
                "source_name": self.SOURCE_NAME,
                "configured": True,
                "available": True,
                "dataset_version": "dr/v3",
                "dataset_date": None,
                "message": "ABDM Drug Registry credentials configured.",
            }
        return {
            "source_name": self.SOURCE_NAME,
            "configured": False,
            "available": False,
            "dataset_version": None,
            "dataset_date": None,
            "message": (
                "ABDM_CLIENT_ID / ABDM_CLIENT_SECRET not configured. "
                "Register at https://sandbox.abdm.gov.in to enable live ABDM Drug Registry queries."
            ),
        }

    def attribution(self) -> dict[str, str | None]:
        return build_source_attribution(
            source_name=self.SOURCE_NAME,
            dataset_version="dr/v3",
            dataset_date=None,
            url="https://drugregistry.abdm.gov.in",
            notes=ABDM_ATTRIBUTION_NOTE,
        )

    async def _get_access_token(self) -> str:
        if not self.is_configured:
            raise ABDMDrugRegistryError("ABDM credentials are not configured.")

        if self._access_token and time.time() < self._token_expires_at - 30:
            return self._access_token

        payload = {
            "clientId": self.client_id,
            "clientSecret": self.client_secret,
            "grantType": "client_credentials",
        }
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds,
                transport=self.transport,
            ) as client:
                response = await client.post(self.gateway_url, json=payload)
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ABDMDrugRegistryError(f"ABDM authentication failed: {exc}") from exc

        token = data.get("accessToken") or data.get("access_token")
        if not token:
            raise ABDMDrugRegistryError("ABDM session response did not contain an access token.")

        expires_in = int(data.get("expiresIn") or data.get("expires_in") or 900)
        self._access_token = str(token)
        self._token_expires_at = time.time() + expires_in
        return self._access_token

    async def search_drugs(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        """Query ABDM Drug Registry when credentials are configured."""
        if not self.is_configured or not query.strip():
            return []

        token = await self._get_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "REQUEST-ID": str(uuid.uuid4()),
            "TIMESTAMP": datetime.now(timezone.utc).isoformat(),
        }
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds,
                transport=self.transport,
            ) as client:
                response = await client.get(
                    f"{self.registry_url}/drugs/search",
                    params={"q": query.strip(), "limit": limit},
                    headers=headers,
                )
                if response.status_code == 404:
                    return []
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ABDMDrugRegistryError(f"ABDM Drug Registry search failed: {exc}") from exc

        if isinstance(data, list):
            return data[:limit]
        if isinstance(data, dict):
            items = data.get("results") or data.get("drugs") or data.get("content") or []
            return list(items)[:limit]
        return []
