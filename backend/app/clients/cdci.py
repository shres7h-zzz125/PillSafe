"""Local loader and search index for official NRCeS Common Drug Codes for India (CDCI) Flat Files."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rapidfuzz import fuzz

from app.core.safety import NRCES_CDCI_ATTRIBUTION_NOTE, build_source_attribution


@dataclass
class CDCIRecord:
    """Normalized record parsed from an official NRCeS CDCI Flat File TSV."""

    code: str
    display_name: str
    generic_name: str | None = None
    brand_name: str | None = None
    substances: list[str] = field(default_factory=list)
    dosage_form: str | None = None
    strength: str | None = None
    manufacturer: str | None = None
    record_type: str = "medicine"


class NRCeSCDCIClient:
    """Reads and indexes official NRCeS CDCI TSV files if present on disk."""

    SOURCE_NAME = "NRCeS Common Drug Codes for India (CDCI)"

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self._records: list[CDCIRecord] = []
        self._by_code: dict[str, CDCIRecord] = {}
        self._dataset_version: str | None = None
        self._dataset_date: str | None = None
        self._loaded_files: list[str] = []
        self._load_if_present()

    @property
    def is_available(self) -> bool:
        return len(self._records) > 0

    def status(self) -> dict[str, Any]:
        if self.is_available:
            return {
                "source_name": self.SOURCE_NAME,
                "configured": True,
                "available": True,
                "dataset_version": self._dataset_version,
                "dataset_date": self._dataset_date,
                "message": (
                    f"Loaded {len(self._records)} records from {len(self._loaded_files)} CDCI file(s)."
                ),
            }
        return {
            "source_name": self.SOURCE_NAME,
            "configured": False,
            "available": False,
            "dataset_version": None,
            "dataset_date": None,
            "message": (
                "CDCI Flat Files not found in backend/data/cdci/. "
                "Register at https://www.nrces.in/ and place official TSV files there to enable local Indian brand lookup."
            ),
        }

    def attribution(self) -> dict[str, str | None]:
        return build_source_attribution(
            source_name=self.SOURCE_NAME,
            dataset_version=self._dataset_version,
            dataset_date=self._dataset_date,
            url="https://www.nrces.in/",
            notes=NRCES_CDCI_ATTRIBUTION_NOTE,
        )

    def _load_if_present(self) -> None:
        if not self.data_dir.exists():
            return

        release_meta = self.data_dir / "release_info.json"
        if release_meta.exists():
            try:
                meta = json.loads(release_meta.read_text(encoding="utf-8"))
                self._dataset_version = meta.get("version")
                self._dataset_date = meta.get("release_date") or meta.get("date")
            except (OSError, ValueError):
                pass

        tsv_files = [
            path
            for path in sorted(self.data_dir.iterdir())
            if path.is_file()
            and path.suffix.lower() in {".tsv", ".txt"}
            and path.name.lower() not in {"license.txt", "readme.txt"}
        ]
        if not tsv_files:
            return

        substances_by_id: dict[str, str] = {}
        forms_by_id: dict[str, str] = {}
        suppliers_by_id: dict[str, str] = {}
        generics_by_id: dict[str, dict[str, Any]] = {}
        brands_by_id: dict[str, str] = {}

        # First pass: load lookup master tables if present
        for path in tsv_files:
            lower = path.name.lower()
            rows = self._read_tsv(path)
            if not rows:
                continue
            self._loaded_files.append(path.name)
            if "substance" in lower:
                for row in rows:
                    key = self._pick(row, ["substance_id", "id", "code", "sctid"])
                    val = self._pick(row, ["substance_name", "name", "term", "preferred_term"])
                    if key and val:
                        substances_by_id[key] = val
            elif "form" in lower:
                for row in rows:
                    key = self._pick(row, ["form_id", "drug_form_id", "id", "code"])
                    val = self._pick(row, ["form_name", "drug_form", "name", "term"])
                    if key and val:
                        forms_by_id[key] = val
            elif "supplier" in lower:
                for row in rows:
                    key = self._pick(row, ["supplier_id", "id", "code"])
                    val = self._pick(row, ["supplier_name", "manufacturer", "name"])
                    if key and val:
                        suppliers_by_id[key] = val
            elif "brand" in lower and "branded" not in lower and "product" not in lower:
                for row in rows:
                    key = self._pick(row, ["brand_id", "id", "code"])
                    val = self._pick(row, ["brand_name", "name", "term"])
                    if key and val:
                        brands_by_id[key] = val

        # Second pass: load generic and branded medicine records
        latest_mtime = 0.0
        for path in tsv_files:
            latest_mtime = max(latest_mtime, path.stat().st_mtime)
            lower = path.name.lower()
            rows = self._read_tsv(path)
            if not rows:
                continue

            if "generic" in lower:
                for row in rows:
                    record = self._row_to_record(
                        row,
                        record_type="generic",
                        substances_by_id=substances_by_id,
                        forms_by_id=forms_by_id,
                        suppliers_by_id=suppliers_by_id,
                        brands_by_id=brands_by_id,
                    )
                    if record:
                        generics_by_id[record.code] = {
                            "generic_name": record.generic_name or record.display_name,
                            "substances": record.substances,
                            "dosage_form": record.dosage_form,
                            "strength": record.strength,
                        }
                        self._add_record(record)
            elif "branded" in lower or "product" in lower or "medicine" in lower:
                for row in rows:
                    record = self._row_to_record(
                        row,
                        record_type="branded",
                        substances_by_id=substances_by_id,
                        forms_by_id=forms_by_id,
                        suppliers_by_id=suppliers_by_id,
                        brands_by_id=brands_by_id,
                        generics_by_id=generics_by_id,
                    )
                    if record:
                        self._add_record(record)

        # Fallback: if only brand or substance masters exist, index them directly
        if not self._records:
            for code, name in brands_by_id.items():
                self._add_record(
                    CDCIRecord(code=code, display_name=name, brand_name=name, record_type="brand")
                )
            for code, name in substances_by_id.items():
                self._add_record(
                    CDCIRecord(
                        code=code,
                        display_name=name,
                        generic_name=name,
                        substances=[name],
                        record_type="substance",
                    )
                )

        if self._dataset_date is None and latest_mtime > 0:
            self._dataset_date = datetime.fromtimestamp(latest_mtime, tz=timezone.utc).date().isoformat()

    def _add_record(self, record: CDCIRecord) -> None:
        self._records.append(record)
        self._by_code[record.code] = record

    @staticmethod
    def _read_tsv(path: Path) -> list[dict[str, str]]:
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle, delimiter="\t")
                return [
                    {str(k).strip().lower(): (v or "").strip() for k, v in row.items() if k is not None}
                    for row in reader
                ]
        except OSError:
            return []

    @staticmethod
    def _pick(row: dict[str, str], keys: list[str]) -> str | None:
        for key in keys:
            val = row.get(key)
            if val:
                return val
        return None

    def _row_to_record(
        self,
        row: dict[str, str],
        *,
        record_type: str,
        substances_by_id: dict[str, str],
        forms_by_id: dict[str, str],
        suppliers_by_id: dict[str, str],
        brands_by_id: dict[str, str],
        generics_by_id: dict[str, dict[str, Any]] | None = None,
    ) -> CDCIRecord | None:
        code = self._pick(
            row,
            [
                "cdci_code",
                "drug_code",
                "product_code",
                "generic_code",
                "sctid",
                "concept_id",
                "id",
                "code",
            ],
        )
        brand_name = self._pick(row, ["brand_name", "product_name", "trade_name"])
        if not brand_name:
            brand_id = self._pick(row, ["brand_id"])
            if brand_id:
                brand_name = brands_by_id.get(brand_id)

        generic_name = self._pick(row, ["generic_name", "clinical_drug", "generic_drug_name"])
        if not generic_name and generics_by_id:
            gen_id = self._pick(row, ["generic_id", "generic_code"])
            if gen_id and gen_id in generics_by_id:
                generic_name = generics_by_id[gen_id].get("generic_name")

        display_name = (
            self._pick(row, ["display_name", "preferred_term", "medicine_name", "name"])
            or brand_name
            or generic_name
        )
        if not code or not display_name:
            return None

        raw_substances = self._pick(
            row,
            ["substance_name", "substances", "active_ingredient", "ingredients", "composition"],
        )
        substances: list[str] = []
        if raw_substances:
            for part in raw_substances.replace("+", "|").replace(";", "|").split("|"):
                cleaned = part.strip()
                if cleaned:
                    substances.append(cleaned)
        else:
            sub_id = self._pick(row, ["substance_id"])
            if sub_id and sub_id in substances_by_id:
                substances.append(substances_by_id[sub_id])
            elif generics_by_id:
                gen_id = self._pick(row, ["generic_id", "generic_code"])
                if gen_id and gen_id in generics_by_id:
                    substances.extend(generics_by_id[gen_id].get("substances") or [])

        dosage_form = self._pick(row, ["dosage_form", "drug_form", "form", "dose_form"])
        if not dosage_form:
            form_id = self._pick(row, ["form_id", "drug_form_id"])
            if form_id:
                dosage_form = forms_by_id.get(form_id)

        strength = self._pick(row, ["strength", "dose_strength", "concentration"])
        manufacturer = self._pick(row, ["manufacturer", "supplier_name", "company"])
        if not manufacturer:
            sup_id = self._pick(row, ["supplier_id"])
            if sup_id:
                manufacturer = suppliers_by_id.get(sup_id)

        return CDCIRecord(
            code=code,
            display_name=display_name,
            generic_name=generic_name,
            brand_name=brand_name,
            substances=substances,
            dosage_form=dosage_form,
            strength=strength,
            manufacturer=manufacturer,
            record_type=record_type,
        )

    def get_by_code(self, code: str) -> CDCIRecord | None:
        return self._by_code.get(code)

    def search(self, query: str, limit: int = 10) -> list[tuple[CDCIRecord, float]]:
        """Search indexed CDCI records using exact and fuzzy matching for OCR text."""
        if not self.is_available or not query.strip():
            return []

        q_lower = query.strip().lower()
        scored: list[tuple[CDCIRecord, float]] = []
        for record in self._records:
            haystack = " ".join(
                part
                for part in [
                    record.display_name,
                    record.brand_name or "",
                    record.generic_name or "",
                    " ".join(record.substances),
                ]
                if part
            ).lower()
            if q_lower in haystack:
                score = 95.0 if record.display_name.lower().startswith(q_lower) else 88.0
            else:
                score = max(
                    float(fuzz.WRatio(q_lower, record.display_name.lower())),
                    float(fuzz.token_set_ratio(q_lower, haystack)),
                )
            if score >= 60.0:
                scored.append((record, round(score, 2)))

        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[:limit]
