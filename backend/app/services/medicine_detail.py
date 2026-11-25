"""Service for fetching detailed medicine information from RxNorm, CDCI, and openFDA."""

from __future__ import annotations

from typing import Any

from app.clients.cdci import NRCeSCDCIClient
from app.clients.openfda import OpenFDAClient, OpenFDAClientError
from app.clients.rxnorm import RxNormClient, RxNormClientError
from app.core.safety import MEDICAL_DISCLAIMER


class MedicineDetailService:
    """Assembles normalized composition, properties, and FDA label details."""

    def __init__(
        self,
        *,
        rxnorm: RxNormClient,
        openfda: OpenFDAClient,
        cdci: NRCeSCDCIClient,
    ) -> None:
        self.rxnorm = rxnorm
        self.openfda = openfda
        self.cdci = cdci

    async def get_detail(self, medicine_id: str) -> dict[str, Any]:
        """Resolve a medicine by canonical ID (rxcui:<id> or cdci:<code>) from real sources."""
        raw_id = medicine_id.strip()
        attributions: list[dict[str, Any]] = []

        if raw_id.startswith("cdci:"):
            code = raw_id.split(":", 1)[1]
            record = self.cdci.get_by_code(code)
            if record is None:
                raise LookupError(f"CDCI medicine '{raw_id}' not found.")

            attributions.append(self.cdci.attribution())
            active_ingredients: list[dict[str, Any]] = []
            resolved_rxcui: str | None = None

            for sub in record.substances:
                sub_rxcui = None
                try:
                    matches = await self.rxnorm.find_rxcui_by_name(sub)
                    if matches:
                        sub_rxcui = matches[0]
                        if resolved_rxcui is None:
                            resolved_rxcui = sub_rxcui
                except RxNormClientError:
                    pass
                active_ingredients.append(
                    {
                        "name": sub,
                        "rxcui": sub_rxcui,
                        "strength": record.strength,
                        "source": self.cdci.SOURCE_NAME,
                    }
                )

            fda_label = await self._fetch_openfda_label(
                rxcui=resolved_rxcui,
                substance_name=record.substances[0] if record.substances else None,
                generic_name=record.generic_name,
            )
            if fda_label is not None:
                attributions.append(fda_label["source_attribution"])

            return {
                "id": raw_id,
                "source": self.cdci.SOURCE_NAME,
                "source_id": record.code,
                "display_name": record.display_name,
                "generic_name": record.generic_name,
                "brand_name": record.brand_name,
                "dosage_form": record.dosage_form,
                "strength": record.strength,
                "manufacturer": record.manufacturer,
                "active_ingredients": active_ingredients,
                "rxcui": resolved_rxcui,
                "cdci_code": record.code,
                "rxnorm_properties": {},
                "fda_label": fda_label,
                "attributions": attributions,
                "requires_confirmation": True,
                "disclaimer": MEDICAL_DISCLAIMER,
            }

        # RxNorm path: supports "rxcui:12345" or "12345"
        rxcui = raw_id.split(":", 1)[1] if raw_id.startswith("rxcui:") else raw_id
        if not rxcui.isdigit():
            raise LookupError(f"Invalid or unsupported medicine ID '{medicine_id}'.")

        try:
            props = await self.rxnorm.get_rxcui_properties(rxcui)
            ver = await self.rxnorm.get_version()
            raw_ings = await self.rxnorm.get_ingredients(rxcui)
            all_props = await self.rxnorm.get_all_properties(rxcui)
        except RxNormClientError as exc:
            raise RuntimeError(f"Failed to query RxNorm for RxCUI '{rxcui}': {exc}") from exc

        if not props or not props.get("name"):
            raise LookupError(f"Medicine with RxCUI '{rxcui}' was not found in RxNorm.")

        attributions.append(self.rxnorm.attribution(dataset_version=ver.get("version")))
        active_ingredients = [
            {
                "name": ing["name"],
                "rxcui": ing.get("rxcui") or None,
                "strength": None,
                "source": self.rxnorm.SOURCE_NAME,
            }
            for ing in raw_ings
        ]
        if not active_ingredients and props.get("tty") in {"IN", "MIN", "PIN"}:
            active_ingredients = [
                {
                    "name": str(props["name"]),
                    "rxcui": rxcui,
                    "strength": None,
                    "source": self.rxnorm.SOURCE_NAME,
                }
            ]

        first_ing = active_ingredients[0]["name"] if active_ingredients else str(props["name"])
        fda_label = await self._fetch_openfda_label(
            rxcui=rxcui,
            substance_name=first_ing,
            generic_name=first_ing,
        )
        if fda_label is not None:
            attributions.append(fda_label["source_attribution"])

        return {
            "id": f"rxcui:{rxcui}",
            "source": self.rxnorm.SOURCE_NAME,
            "source_id": rxcui,
            "display_name": str(props["name"]),
            "generic_name": ", ".join(i["name"] for i in active_ingredients) or str(props["name"]),
            "brand_name": None,
            "dosage_form": None,
            "strength": None,
            "manufacturer": None,
            "active_ingredients": active_ingredients,
            "rxcui": rxcui,
            "cdci_code": None,
            "rxnorm_properties": {
                "tty": props.get("tty"),
                "synonym": props.get("synonym"),
                "prop_concepts": all_props.get("propConcept", [])[:25],
            },
            "fda_label": fda_label,
            "attributions": attributions,
            "requires_confirmation": True,
            "disclaimer": MEDICAL_DISCLAIMER,
        }

    async def _fetch_openfda_label(
        self,
        *,
        rxcui: str | None,
        substance_name: str | None,
        generic_name: str | None,
    ) -> dict[str, Any] | None:
        try:
            label = await self.openfda.search_label(
                rxcui=rxcui,
                substance_name=substance_name,
                generic_name=generic_name,
                limit=1,
            )
        except OpenFDAClientError:
            return None
        if not label:
            return None
        return {
            "warnings": label.get("warnings") or [],
            "boxed_warning": label.get("boxed_warning") or [],
            "contraindications": label.get("contraindications") or [],
            "dosage_and_administration": label.get("dosage_and_administration") or [],
            "drug_interactions_label_text": label.get("drug_interactions_label_text") or [],
            "source_attribution": self.openfda.attribution(dataset_date=label.get("last_updated")),
        }
