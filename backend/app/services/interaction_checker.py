"""Interaction and duplicate-ingredient checking service with strict safety guardrails."""

from __future__ import annotations

import re
from collections import defaultdict
from itertools import combinations
from typing import Any

from app.clients.cdci import NRCeSCDCIClient
from app.clients.interactions import InteractionProvider
from app.clients.openfda import OpenFDAClient, OpenFDAClientError
from app.clients.rxnorm import RxNormClient, RxNormClientError
from app.core.cache import SQLiteCache
from app.core.safety import (
    AMBER_STATUS_COLOR,
    COULD_NOT_CHECK_CODE,
    COULD_NOT_CHECK_LABEL,
    GREY_STATUS_COLOR,
    MEDICAL_DISCLAIMER,
    RED_STATUS_COLOR,
    build_source_attribution,
)


class ConfirmationRequiredError(ValueError):
    """Raised when an unconfirmed medicine is submitted for checking."""


_SALT_SUFFIXES = (
    "hydrochloride",
    "hcl",
    "sodium",
    "potassium",
    "maleate",
    "tartrate",
    "sulfate",
    "sulphate",
    "phosphate",
    "citrate",
    "acetate",
    "fumarate",
    "mesylate",
    "besylate",
    "monohydrate",
    "dihydrate",
)


def _normalize_ingredient_name(name: str) -> str:
    cleaned = re.sub(r"\s+", " ", name.lower()).strip()
    for suffix in _SALT_SUFFIXES:
        cleaned = re.sub(rf"\b{suffix}\b", "", cleaned).strip()
    return re.sub(r"\s+", " ", cleaned).strip()


class InteractionCheckerService:
    """Detects duplicate active ingredients and checks pairs against verified interaction providers.

    Never fabricates interactions and never claims a medicine or combination is safe.
    """

    def __init__(
        self,
        *,
        rxnorm: RxNormClient,
        openfda: OpenFDAClient,
        cdci: NRCeSCDCIClient,
        interaction_provider: InteractionProvider,
        cache: SQLiteCache,
    ) -> None:
        self.rxnorm = rxnorm
        self.openfda = openfda
        self.cdci = cdci
        self.interaction_provider = interaction_provider
        self.cache = cache

    async def check(
        self,
        *,
        medicines: list[dict[str, Any]] | None,
        use_saved_list: bool = False,
    ) -> dict[str, Any]:
        """Run duplicate-ingredient and verified pairwise interaction checks."""
        if use_saved_list and not medicines:
            resolved_medicines = self.cache.list_confirmed_medicines()
            if not resolved_medicines:
                raise ValueError("The user's confirmed medicine list is empty.")
        else:
            if not medicines:
                raise ValueError("Provide at least one confirmed medicine or set use_saved_list=true.")
            resolved_medicines = []
            for item in medicines:
                if not item.get("confirmed", False):
                    raise ConfirmationRequiredError(
                        "All medicines must be explicitly confirmed by the user (confirmed=true) before checking."
                    )
                resolved_medicines.append(await self._resolve_item(item))

        sources_checked: list[dict[str, Any]] = []
        attributions: list[dict[str, Any]] = []
        seen_attr: set[str] = set()

        def _add_attr(attr: dict[str, Any] | None) -> None:
            if not attr:
                return
            name = str(attr.get("source_name") or "")
            if name and name not in seen_attr:
                seen_attr.add(name)
                attributions.append(attr)

        # Record sources inspected
        rx_version: str | None = None
        try:
            rx_version = (await self.rxnorm.get_version()).get("version")
            rx_status = {
                "source_name": self.rxnorm.SOURCE_NAME,
                "configured": True,
                "available": True,
                "dataset_version": rx_version,
                "dataset_date": None,
                "message": "Used for ingredient normalization (DDI API discontinued by NLM in Jan 2024).",
            }
        except RxNormClientError as exc:
            rx_status = {
                "source_name": self.rxnorm.SOURCE_NAME,
                "configured": True,
                "available": False,
                "dataset_version": None,
                "dataset_date": None,
                "message": str(exc),
            }
        sources_checked.append(rx_status)
        _add_attr(self.rxnorm.attribution(dataset_version=rx_version))

        provider_status = self.interaction_provider.status()
        sources_checked.append(provider_status)

        # 1. Duplicate active-ingredient detection across confirmed medicines
        ingredient_map: dict[str, list[dict[str, str]]] = defaultdict(list)
        for med in resolved_medicines:
            med_name = str(med.get("display_name") or med.get("id") or "Unknown medicine")
            med_ings = med.get("active_ingredients") or []
            seen_in_this_med: set[str] = set()
            for ing in med_ings:
                ing_name = str(ing.get("name") or ing) if isinstance(ing, dict) else str(ing)
                norm = _normalize_ingredient_name(ing_name)
                if not norm or norm in seen_in_this_med:
                    continue
                seen_in_this_med.add(norm)
                ingredient_map[norm].append({"medicine": med_name, "raw_ingredient": ing_name})

        duplicate_alerts: list[dict[str, Any]] = []
        for norm_ing, occurrences in ingredient_map.items():
            if len(occurrences) >= 2:
                meds_involved = [occ["medicine"] for occ in occurrences]
                display_ing = occurrences[0]["raw_ingredient"]
                duplicate_alerts.append(
                    {
                        "ingredient": display_ing,
                        "normalized_ingredient": norm_ing,
                        "medicines_involved": meds_involved,
                        "severity": "warning",
                        "badge_color": AMBER_STATUS_COLOR,
                        "message": (
                            f"Duplicate active ingredient '{display_ing}' detected across "
                            f"{len(meds_involved)} confirmed medicines: {', '.join(meds_involved)}. "
                            "Combining medicines with the same active ingredient may cause unintended overdose."
                        ),
                        "source_attribution": build_source_attribution(
                            source_name="Verified Medicine Composition (RxNorm / CDCI)",
                            dataset_version=rx_version,
                            notes="Detected by comparing normalized active ingredients across confirmed medicines.",
                        ),
                    }
                )

        # 2. Pairwise interaction checks using ONLY verified, licensed interaction provider
        pair_results: list[dict[str, Any]] = []
        any_interaction_found = False
        any_could_not_check = False

        for med_a, med_b in combinations(resolved_medicines, 2):
            name_a = str(med_a.get("display_name") or med_a.get("id"))
            name_b = str(med_b.get("display_name") or med_b.get("id"))
            ings_a = self._ingredient_names(med_a)
            ings_b = self._ingredient_names(med_b)

            if not provider_status.get("available"):
                any_could_not_check = True
                pair_results.append(
                    {
                        "medicine_a": name_a,
                        "medicine_b": name_b,
                        "ingredients_a": ings_a,
                        "ingredients_b": ings_b,
                        "status": COULD_NOT_CHECK_LABEL,
                        "status_code": COULD_NOT_CHECK_CODE,
                        "badge_color": GREY_STATUS_COLOR,
                        "reason": str(provider_status.get("message") or "No verified interaction source configured."),
                        "description": None,
                        "source_attribution": None,
                    }
                )
                continue

            # Query verified provider per ingredient pair
            found_payload: dict[str, Any] | None = None
            for ing_a in (ings_a or [name_a]):
                for ing_b in (ings_b or [name_b]):
                    result = await self.interaction_provider.check_pair(
                        ingredient_a=ing_a,
                        ingredient_b=ing_b,
                        rxcui_a=med_a.get("rxcui"),
                        rxcui_b=med_b.get("rxcui"),
                    )
                    if result is not None:
                        found_payload = result
                        break
                if found_payload is not None:
                    break

            if found_payload is None:
                any_could_not_check = True
                pair_results.append(
                    {
                        "medicine_a": name_a,
                        "medicine_b": name_b,
                        "ingredients_a": ings_a,
                        "ingredients_b": ings_b,
                        "status": COULD_NOT_CHECK_LABEL,
                        "status_code": COULD_NOT_CHECK_CODE,
                        "badge_color": GREY_STATUS_COLOR,
                        "reason": "Pair could not be verified in the configured interaction source.",
                        "description": None,
                        "source_attribution": None,
                    }
                )
            else:
                interactions_list = found_payload.get("interactions") or []
                provider_attr = (
                    self.interaction_provider.attribution()
                    if hasattr(self.interaction_provider, "attribution")
                    else None
                )
                _add_attr(provider_attr)
                if interactions_list:
                    any_interaction_found = True
                    first = interactions_list[0]
                    severity = str(first.get("severity") or "Interaction warning")
                    pair_results.append(
                        {
                            "medicine_a": name_a,
                            "medicine_b": name_b,
                            "ingredients_a": ings_a,
                            "ingredients_b": ings_b,
                            "status": severity,
                            "status_code": "interaction_found",
                            "badge_color": RED_STATUS_COLOR,
                            "reason": "Interaction reported by verified licensed source.",
                            "description": first.get("description"),
                            "source_attribution": provider_attr,
                        }
                    )
                else:
                    # Even if a licensed source returned 0 interactions, never label as "safe"
                    pair_results.append(
                        {
                            "medicine_a": name_a,
                            "medicine_b": name_b,
                            "ingredients_a": ings_a,
                            "ingredients_b": ings_b,
                            "status": "No interaction recorded in queried source (not a safety guarantee)",
                            "status_code": "no_recorded_interaction_in_source",
                            "badge_color": GREY_STATUS_COLOR,
                            "reason": "Queried source returned no interaction record for this pair; absence of data does not guarantee safety.",
                            "description": None,
                            "source_attribution": provider_attr,
                        }
                    )

        # 3. Optional openFDA label reference excerpts (regulatory prose only)
        fda_notes: list[dict[str, Any]] = []
        fda_available = True
        fda_msg = "Queried openFDA drug labels for official interaction reference text."
        for med in resolved_medicines:
            ings = self._ingredient_names(med)
            try:
                label = await self.openfda.search_label(
                    rxcui=med.get("rxcui"),
                    substance_name=ings[0] if ings else None,
                    generic_name=med.get("generic_name"),
                    limit=1,
                )
            except OpenFDAClientError as exc:
                fda_available = False
                fda_msg = str(exc)
                label = None
            if label and label.get("drug_interactions_label_text"):
                attr = self.openfda.attribution(dataset_date=label.get("last_updated"))
                _add_attr(attr)
                fda_notes.append(
                    {
                        "medicine": med.get("display_name"),
                        "label_excerpts": label["drug_interactions_label_text"],
                        "source_attribution": attr,
                    }
                )
        sources_checked.append(
            {
                "source_name": self.openfda.SOURCE_NAME,
                "configured": True,
                "available": fda_available,
                "dataset_version": None,
                "dataset_date": fda_notes[0]["source_attribution"].get("dataset_date") if fda_notes else None,
                "message": fda_msg,
            }
        )

        # 4. Overall status (never claims "safe")
        if any_interaction_found:
            overall_status = "Interaction warning"
            overall_code = "interaction_found"
            badge_color = RED_STATUS_COLOR
        elif duplicate_alerts and (any_could_not_check or not pair_results):
            overall_status = (
                "Duplicate active ingredient detected; pairwise drug interactions: Could not check"
            )
            overall_code = "duplicate_and_could_not_check"
            badge_color = AMBER_STATUS_COLOR
        elif duplicate_alerts:
            overall_status = "Duplicate active ingredient detected"
            overall_code = "duplicate_ingredient_detected"
            badge_color = AMBER_STATUS_COLOR
        else:
            overall_status = COULD_NOT_CHECK_LABEL
            overall_code = COULD_NOT_CHECK_CODE
            badge_color = GREY_STATUS_COLOR

        return {
            "overall_status": overall_status,
            "overall_status_code": overall_code,
            "badge_color": badge_color,
            "checked_medicines": resolved_medicines,
            "duplicate_ingredients": duplicate_alerts,
            "pair_results": pair_results,
            "fda_label_interaction_notes": fda_notes,
            "sources_checked": sources_checked,
            "attributions": attributions,
            "disclaimer": MEDICAL_DISCLAIMER,
        }

    async def _resolve_item(self, item: dict[str, Any]) -> dict[str, Any]:
        med_id = item.get("medicine_id")
        if med_id:
            saved = self.cache.get_confirmed_medicine(med_id)
            if saved is not None:
                if item.get("user_dosage_note"):
                    saved["user_dosage_note"] = item["user_dosage_note"]
                return saved

            if str(med_id).startswith("cdci:"):
                code = str(med_id).split(":", 1)[1]
                record = self.cdci.get_by_code(code)
                if record is not None:
                    return {
                        "id": med_id,
                        "source": self.cdci.SOURCE_NAME,
                        "source_id": record.code,
                        "display_name": record.display_name,
                        "generic_name": record.generic_name,
                        "brand_name": record.brand_name,
                        "dosage_form": record.dosage_form,
                        "strength": record.strength,
                        "active_ingredients": [
                            {"name": sub, "rxcui": None, "strength": record.strength, "source": self.cdci.SOURCE_NAME}
                            for sub in record.substances
                        ],
                        "rxcui": item.get("rxcui"),
                        "cdci_code": record.code,
                        "user_dosage_note": item.get("user_dosage_note"),
                    }

        rxcui = item.get("rxcui")
        if not rxcui and med_id and str(med_id).startswith("rxcui:"):
            rxcui = str(med_id).split(":", 1)[1]

        ingredients = [
            {"name": ing, "rxcui": None, "strength": None, "source": "confirmed_input"}
            for ing in (item.get("active_ingredients") or [])
            if str(ing).strip()
        ]
        display_name = item.get("display_name")

        if rxcui:
            try:
                props = await self.rxnorm.get_rxcui_properties(str(rxcui))
                if props and not display_name:
                    display_name = props.get("name")
                if not ingredients:
                    raw_ings = await self.rxnorm.get_ingredients(str(rxcui))
                    ingredients = [
                        {
                            "name": ing["name"],
                            "rxcui": ing.get("rxcui") or None,
                            "strength": None,
                            "source": self.rxnorm.SOURCE_NAME,
                        }
                        for ing in raw_ings
                    ]
                    if not ingredients and props and props.get("tty") in {"IN", "MIN", "PIN"}:
                        ingredients = [
                            {
                                "name": str(props["name"]),
                                "rxcui": str(rxcui),
                                "strength": None,
                                "source": self.rxnorm.SOURCE_NAME,
                            }
                        ]
            except RxNormClientError:
                pass

        return {
            "id": med_id or (f"rxcui:{rxcui}" if rxcui else (display_name or "confirmed-item")),
            "source": "confirmed_input",
            "source_id": str(rxcui or med_id or display_name or ""),
            "display_name": display_name or str(med_id or rxcui or "Unknown medicine"),
            "generic_name": ", ".join(i["name"] for i in ingredients) or None,
            "brand_name": None,
            "dosage_form": None,
            "strength": None,
            "active_ingredients": ingredients,
            "rxcui": str(rxcui) if rxcui else None,
            "cdci_code": item.get("cdci_code"),
            "user_dosage_note": item.get("user_dosage_note"),
        }

    @staticmethod
    def _ingredient_names(med: dict[str, Any]) -> list[str]:
        result: list[str] = []
        for ing in med.get("active_ingredients") or []:
            name = ing.get("name") if isinstance(ing, dict) else str(ing)
            if name and name not in result:
                result.append(name)
        return result
