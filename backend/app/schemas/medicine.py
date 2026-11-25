"""Pydantic models for medicine search, confirmation, details, and interaction checks."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SourceAttribution(BaseModel):
    """Attribution and dataset provenance metadata."""

    source_name: str
    dataset_version: str | None = None
    dataset_date: str | None = None
    url: str | None = None
    notes: str | None = None


class SourceStatus(BaseModel):
    """Availability status of an upstream or local medicine data source."""

    source_name: str
    configured: bool
    available: bool
    dataset_version: str | None = None
    dataset_date: str | None = None
    message: str | None = None


class ActiveIngredient(BaseModel):
    """Normalized active pharmaceutical ingredient."""

    name: str
    rxcui: str | None = None
    strength: str | None = None
    source: str


class MedicineSearchCandidate(BaseModel):
    """Candidate medicine returned by search, awaiting user confirmation."""

    id: str = Field(..., description="Canonical PillSafe medicine ID (e.g. rxcui:<id> or cdci:<code>).")
    source: str
    source_id: str
    display_name: str
    generic_name: str | None = None
    brand_name: str | None = None
    dosage_form: str | None = None
    strength: str | None = None
    active_ingredients: list[ActiveIngredient] = Field(default_factory=list)
    rxcui: str | None = None
    cdci_code: str | None = None
    match_score: float = Field(..., ge=0.0, le=100.0)
    matched_query: str
    requires_confirmation: bool = True
    source_attribution: SourceAttribution


class MedicineSearchResponse(BaseModel):
    """Response envelope for OCR-compatible medicine search."""

    query: str
    normalized_query: str
    extracted_tokens: list[str] = Field(default_factory=list)
    is_ocr: bool = False
    total: int
    results: list[MedicineSearchCandidate] = Field(default_factory=list)
    source_statuses: list[SourceStatus] = Field(default_factory=list)
    attributions: list[SourceAttribution] = Field(default_factory=list)
    disclaimer: str


class MedicineLabelInfo(BaseModel):
    """Official regulatory drug label sections (e.g., from openFDA SPL)."""

    warnings: list[str] = Field(default_factory=list)
    boxed_warning: list[str] = Field(default_factory=list)
    contraindications: list[str] = Field(default_factory=list)
    dosage_and_administration: list[str] = Field(default_factory=list)
    drug_interactions_label_text: list[str] = Field(default_factory=list)
    source_attribution: SourceAttribution


class MedicineDetailResponse(BaseModel):
    """Detailed medicine record assembled from verified sources."""

    id: str
    source: str
    source_id: str
    display_name: str
    generic_name: str | None = None
    brand_name: str | None = None
    dosage_form: str | None = None
    strength: str | None = None
    manufacturer: str | None = None
    active_ingredients: list[ActiveIngredient] = Field(default_factory=list)
    rxcui: str | None = None
    cdci_code: str | None = None
    rxnorm_properties: dict[str, Any] = Field(default_factory=dict)
    fda_label: MedicineLabelInfo | None = None
    attributions: list[SourceAttribution] = Field(default_factory=list)
    requires_confirmation: bool = True
    disclaimer: str


class ConfirmMedicineRequest(BaseModel):
    """Explicit confirmation payload before adding a medicine to the user's list."""

    medicine_id: str = Field(..., min_length=1)
    confirmed: bool = Field(..., description="Must be true to add the medicine to the user's list.")
    user_dosage_note: str | None = Field(default=None, max_length=250)


class ConfirmedMedicineEntry(BaseModel):
    """A medicine verified from real sources and confirmed by the user."""

    id: str
    source: str
    source_id: str
    display_name: str
    generic_name: str | None = None
    brand_name: str | None = None
    dosage_form: str | None = None
    strength: str | None = None
    active_ingredients: list[ActiveIngredient] = Field(default_factory=list)
    rxcui: str | None = None
    cdci_code: str | None = None
    source_attribution: SourceAttribution
    confirmed_at: str
    user_dosage_note: str | None = None


class CheckMedicineItem(BaseModel):
    """A confirmed medicine submitted for interaction and duplicate-ingredient checking."""

    medicine_id: str | None = None
    display_name: str | None = None
    rxcui: str | None = None
    cdci_code: str | None = None
    active_ingredients: list[str] = Field(default_factory=list)
    confirmed: bool = Field(
        default=False,
        description="User confirmation is mandatory before checking.",
    )
    user_dosage_note: str | None = None


class CheckRequest(BaseModel):
    """Request body for POST /api/v1/check."""

    medicines: list[CheckMedicineItem] | None = None
    use_saved_list: bool = Field(
        default=False,
        description="If true and medicines is omitted, check the user's saved confirmed medicine list.",
    )


class DuplicateIngredientAlert(BaseModel):
    """Alert raised when two or more confirmed medicines share an active ingredient."""

    ingredient: str
    normalized_ingredient: str
    medicines_involved: list[str]
    severity: str = "warning"
    badge_color: str = "amber"
    message: str
    source_attribution: SourceAttribution


class InteractionPairResult(BaseModel):
    """Pairwise interaction result. Defaults to grey 'Could not check' when unverified."""

    medicine_a: str
    medicine_b: str
    ingredients_a: list[str] = Field(default_factory=list)
    ingredients_b: list[str] = Field(default_factory=list)
    status: str
    status_code: str
    badge_color: str
    reason: str
    description: str | None = None
    source_attribution: SourceAttribution | None = None


class CheckResponse(BaseModel):
    """Response for POST /api/v1/check. Never claims a combination is safe."""

    overall_status: str
    overall_status_code: str
    badge_color: str
    checked_medicines: list[ConfirmedMedicineEntry | dict[str, Any]]
    duplicate_ingredients: list[DuplicateIngredientAlert] = Field(default_factory=list)
    pair_results: list[InteractionPairResult] = Field(default_factory=list)
    fda_label_interaction_notes: list[dict[str, Any]] = Field(default_factory=list)
    sources_checked: list[SourceStatus] = Field(default_factory=list)
    attributions: list[SourceAttribution] = Field(default_factory=list)
    disclaimer: str


class HealthResponse(BaseModel):
    """Health and data-source status response for GET /health."""

    status: str
    app_name: str
    version: str
    environment: str
    cache: dict[str, Any]
    sources: list[SourceStatus]
    disclaimer: str
