"""OCR-compatible medicine search and user confirmation workflow."""

from __future__ import annotations

from typing import Any

from app.clients.abdm import ABDMDrugRegistryClient, ABDMDrugRegistryError
from app.clients.cdci import CDCIRecord, NRCeSCDCIClient
from app.clients.rxnorm import RxNormClient, RxNormClientError
from app.core.cache import SQLiteCache
from app.core.safety import MEDICAL_DISCLAIMER
from app.services.ocr_normalizer import normalize_ocr_text


class MedicineSearchService:
    """Searches real Indian and global medicine sources and enforces confirmation before adding."""

    def __init__(
        self,
        *,
        rxnorm: RxNormClient,
        cdci: NRCeSCDCIClient,
        abdm: ABDMDrugRegistryClient,
        cache: SQLiteCache,
    ) -> None:
        self.rxnorm = rxnorm
        self.cdci = cdci
        self.abdm = abdm
        self.cache = cache

    async def search(
        self,
        *,
        query: str,
        is_ocr: bool = False,
        limit: int = 10,
    ) -> dict[str, Any]:
        """Search across available real medicine sources using raw or OCR-normalized text."""
        use_ocr = is_ocr or ("\n" in query)
        ocr_info = normalize_ocr_text(query, is_ocr=use_ocr)
        normalized_query = str(ocr_info["normalized_query"])
        search_terms: list[str] = list(ocr_info["search_terms"]) or ([query.strip()] if query.strip() else [])

        candidates: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        source_statuses: list[dict[str, Any]] = []
        attributions: list[dict[str, Any]] = []
        seen_attr_names: set[str] = set()

        def _add_attribution(attr: dict[str, Any]) -> None:
            name = str(attr.get("source_name") or "")
            if name and name not in seen_attr_names:
                seen_attr_names.add(name)
                attributions.append(attr)

        # 1. Local NRCeS CDCI Indian medicine lookup (if official TSVs are present)
        cdci_status = self.cdci.status()
        source_statuses.append(cdci_status)
        if self.cdci.is_available:
            _add_attribution(self.cdci.attribution())
            for term in search_terms[:4]:
                for record, score in self.cdci.search(term, limit=limit):
                    cid = f"cdci:{record.code}"
                    if cid in seen_ids:
                        continue
                    seen_ids.add(cid)
                    candidates.append(self._cdci_to_candidate(record, score=score, matched_query=term))

        # 2. ABDM Drug Registry lookup (if credentials are configured)
        abdm_status = self.abdm.status()
        if self.abdm.is_configured:
            try:
                abdm_items = await self.abdm.search_drugs(normalized_query or query, limit=limit)
                source_statuses.append(abdm_status)
                if abdm_items:
                    _add_attribution(self.abdm.attribution())
                for item in abdm_items:
                    code = str(item.get("id") or item.get("code") or item.get("drugCode") or "")
                    name = str(item.get("name") or item.get("brandName") or item.get("genericName") or "")
                    if not code or not name:
                        continue
                    cid = f"abdm:{code}"
                    if cid in seen_ids:
                        continue
                    seen_ids.add(cid)
                    substances = item.get("substances") or item.get("ingredients") or []
                    if isinstance(substances, str):
                        substances = [s.strip() for s in substances.split("+") if s.strip()]
                    candidates.append(
                        {
                            "id": cid,
                            "source": self.abdm.SOURCE_NAME,
                            "source_id": code,
                            "display_name": name,
                            "generic_name": item.get("genericName"),
                            "brand_name": item.get("brandName"),
                            "dosage_form": item.get("dosageForm"),
                            "strength": item.get("strength"),
                            "active_ingredients": [
                                {"name": str(s), "rxcui": None, "strength": None, "source": self.abdm.SOURCE_NAME}
                                for s in substances
                            ],
                            "rxcui": None,
                            "cdci_code": None,
                            "match_score": 85.0,
                            "matched_query": normalized_query or query,
                            "requires_confirmation": True,
                            "source_attribution": self.abdm.attribution(),
                        }
                    )
            except ABDMDrugRegistryError as exc:
                abdm_status["available"] = False
                abdm_status["message"] = str(exc)
                source_statuses.append(abdm_status)
        else:
            source_statuses.append(abdm_status)

        # 3. NLM RxNorm live REST API (free, public)
        rx_version: str | None = None
        rx_available = True
        rx_error_msg: str | None = None
        try:
            ver_info = await self.rxnorm.get_version()
            rx_version = ver_info.get("version")
            rx_attr = self.rxnorm.attribution(dataset_version=rx_version)
            _add_attribution(rx_attr)

            for term in search_terms[:4]:
                # Exact/product drug lookup
                drugs = await self.rxnorm.get_drugs(term)
                for drug in drugs[:limit]:
                    rxcui = str(drug.get("rxcui") or "")
                    name = str(drug.get("name") or "").strip()
                    if not rxcui or not name:
                        continue
                    cid = f"rxcui:{rxcui}"
                    if cid in seen_ids:
                        continue
                    seen_ids.add(cid)
                    candidates.append(
                        await self._build_rxnorm_candidate(
                            rxcui=rxcui,
                            display_name=name,
                            score=90.0,
                            matched_query=term,
                            attribution=rx_attr,
                        )
                    )

                # Approximate/fuzzy lookup (especially useful for noisy OCR text)
                approx = await self.rxnorm.approximate_term(term, max_entries=min(limit, 8))
                for cand in approx:
                    rxcui = str(cand.get("rxcui") or "")
                    if not rxcui:
                        continue
                    cid = f"rxcui:{rxcui}"
                    if cid in seen_ids:
                        continue
                    props = await self.rxnorm.get_rxcui_properties(rxcui)
                    display_name = (props or {}).get("name") or cand.get("name")
                    if not display_name:
                        continue
                    seen_ids.add(cid)
                    raw_score = float(cand.get("score") or 50.0)
                    normalized_score = min(100.0, max(1.0, raw_score))
                    candidates.append(
                        await self._build_rxnorm_candidate(
                            rxcui=rxcui,
                            display_name=str(display_name),
                            score=round(normalized_score, 2),
                            matched_query=term,
                            attribution=rx_attr,
                        )
                    )
                    if len(candidates) >= limit * 2:
                        break
        except RxNormClientError as exc:
            rx_available = False
            rx_error_msg = str(exc)

        source_statuses.append(
            {
                "source_name": self.rxnorm.SOURCE_NAME,
                "configured": True,
                "available": rx_available,
                "dataset_version": rx_version,
                "dataset_date": None,
                "message": rx_error_msg or "RxNorm REST API reachable.",
            }
        )

        candidates.sort(key=lambda c: c["match_score"], reverse=True)
        trimmed = candidates[:limit]

        return {
            "query": query,
            "normalized_query": normalized_query,
            "extracted_tokens": list(ocr_info["extracted_tokens"]),
            "is_ocr": use_ocr,
            "total": len(trimmed),
            "results": trimmed,
            "source_statuses": source_statuses,
            "attributions": attributions,
            "disclaimer": MEDICAL_DISCLAIMER,
        }

    def _cdci_to_candidate(
        self,
        record: CDCIRecord,
        *,
        score: float,
        matched_query: str,
    ) -> dict[str, Any]:
        attr = self.cdci.attribution()
        return {
            "id": f"cdci:{record.code}",
            "source": self.cdci.SOURCE_NAME,
            "source_id": record.code,
            "display_name": record.display_name,
            "generic_name": record.generic_name,
            "brand_name": record.brand_name,
            "dosage_form": record.dosage_form,
            "strength": record.strength,
            "active_ingredients": [
                {
                    "name": sub,
                    "rxcui": None,
                    "strength": record.strength,
                    "source": self.cdci.SOURCE_NAME,
                }
                for sub in record.substances
            ],
            "rxcui": None,
            "cdci_code": record.code,
            "match_score": score,
            "matched_query": matched_query,
            "requires_confirmation": True,
            "source_attribution": attr,
        }

    async def _build_rxnorm_candidate(
        self,
        *,
        rxcui: str,
        display_name: str,
        score: float,
        matched_query: str,
        attribution: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            raw_ingredients = await self.rxnorm.get_ingredients(rxcui)
        except RxNormClientError:
            raw_ingredients = []

        active_ingredients = [
            {
                "name": ing["name"],
                "rxcui": ing.get("rxcui") or None,
                "strength": None,
                "source": self.rxnorm.SOURCE_NAME,
            }
            for ing in raw_ingredients
        ]
        generic_name = (
            ", ".join(ing["name"] for ing in active_ingredients) if active_ingredients else None
        )
        return {
            "id": f"rxcui:{rxcui}",
            "source": self.rxnorm.SOURCE_NAME,
            "source_id": rxcui,
            "display_name": display_name,
            "generic_name": generic_name,
            "brand_name": None,
            "dosage_form": None,
            "strength": None,
            "active_ingredients": active_ingredients,
            "rxcui": rxcui,
            "cdci_code": None,
            "match_score": score,
            "matched_query": matched_query,
            "requires_confirmation": True,
            "source_attribution": attribution,
        }

    async def confirm_and_add(
        self,
        *,
        medicine_id: str,
        confirmed: bool,
        user_dosage_note: str | None = None,
    ) -> dict[str, Any]:
        """Verify medicine ID against a real source and persist it only after explicit confirmation."""
        if not confirmed:
            raise ValueError("Explicit user confirmation (confirmed=true) is required before adding a medicine.")

        normalized_id = medicine_id.strip()
        if normalized_id.startswith("cdci:"):
            code = normalized_id.split(":", 1)[1]
            record = self.cdci.get_by_code(code)
            if record is None:
                raise LookupError(f"CDCI medicine '{normalized_id}' was not found in local dataset.")

            # Optionally resolve RxCUI for Indian substances via RxNorm
            ingredients: list[dict[str, Any]] = []
            resolved_rxcui: str | None = None
            for sub in record.substances:
                rxcui = None
                try:
                    ids = await self.rxnorm.find_rxcui_by_name(sub)
                    if ids:
                        rxcui = ids[0]
                        if resolved_rxcui is None:
                            resolved_rxcui = rxcui
                except RxNormClientError:
                    pass
                ingredients.append(
                    {
                        "name": sub,
                        "rxcui": rxcui,
                        "strength": record.strength,
                        "source": self.cdci.SOURCE_NAME,
                    }
                )

            entry = {
                "id": normalized_id,
                "source": self.cdci.SOURCE_NAME,
                "source_id": record.code,
                "display_name": record.display_name,
                "generic_name": record.generic_name,
                "brand_name": record.brand_name,
                "dosage_form": record.dosage_form,
                "strength": record.strength,
                "active_ingredients": ingredients,
                "rxcui": resolved_rxcui,
                "cdci_code": record.code,
                "source_attribution": self.cdci.attribution(),
                "user_dosage_note": user_dosage_note,
            }
            return self.cache.add_confirmed_medicine(entry)

        # Default: RxNorm lookup (accepts "rxcui:123" or bare numeric RxCUI)
        rxcui = normalized_id.split(":", 1)[1] if normalized_id.startswith("rxcui:") else normalized_id
        if not rxcui.isdigit():
            raise LookupError(f"Unsupported or unknown medicine identifier '{medicine_id}'.")

        try:
            props = await self.rxnorm.get_rxcui_properties(rxcui)
            ver = await self.rxnorm.get_version()
            raw_ings = await self.rxnorm.get_ingredients(rxcui)
        except RxNormClientError as exc:
            raise LookupError(f"Could not verify RxCUI '{rxcui}': {exc}") from exc

        if not props or not props.get("name"):
            raise LookupError(f"RxCUI '{rxcui}' was not found in RxNorm.")

        active_ingredients = [
            {
                "name": ing["name"],
                "rxcui": ing.get("rxcui") or None,
                "strength": None,
                "source": self.rxnorm.SOURCE_NAME,
            }
            for ing in raw_ings
        ]
        # If the RxCUI itself is already an ingredient (IN/MIN/PIN), include it directly
        if not active_ingredients and props.get("tty") in {"IN", "MIN", "PIN"}:
            active_ingredients = [
                {
                    "name": str(props["name"]),
                    "rxcui": rxcui,
                    "strength": None,
                    "source": self.rxnorm.SOURCE_NAME,
                }
            ]

        entry = {
            "id": f"rxcui:{rxcui}",
            "source": self.rxnorm.SOURCE_NAME,
            "source_id": rxcui,
            "display_name": str(props["name"]),
            "generic_name": ", ".join(i["name"] for i in active_ingredients) or str(props["name"]),
            "brand_name": None,
            "dosage_form": None,
            "strength": None,
            "active_ingredients": active_ingredients,
            "rxcui": rxcui,
            "cdci_code": None,
            "source_attribution": self.rxnorm.attribution(dataset_version=ver.get("version")),
            "user_dosage_note": user_dosage_note,
        }
        return self.cache.add_confirmed_medicine(entry)
