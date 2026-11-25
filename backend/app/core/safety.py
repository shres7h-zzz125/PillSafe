"""PillSafe safety constants, disclaimers, and source attribution helpers."""

from __future__ import annotations

COULD_NOT_CHECK_LABEL = "Could not check"
COULD_NOT_CHECK_CODE = "could_not_check"
GREY_STATUS_COLOR = "grey"
AMBER_STATUS_COLOR = "amber"
RED_STATUS_COLOR = "red"

MEDICAL_DISCLAIMER = (
    "PillSafe provides medicine reference information from official and public "
    "sources only. It does not provide medical advice, diagnosis, or any "
    "guarantee that a medicine or combination is safe. Always consult a "
    "qualified doctor or pharmacist before starting, stopping, or combining medicines."
)

OPENFDA_DISCLAIMER = (
    "openFDA data is derived from US FDA Structured Product Labeling for "
    "informational purposes only and has not been validated for autonomous "
    "clinical diagnosis or treatment decisions."
)

RXNORM_ATTRIBUTION_NOTE = (
    "This product uses publicly available data from the U.S. National Library of Medicine (NLM), "
    "National Institutes of Health, Department of Health and Human Services; NLM is not responsible "
    "for the product and does not endorse or recommend this or any other product."
)

NRCES_CDCI_ATTRIBUTION_NOTE = (
    "Common Drug Codes for India (CDCI) published by the National Resource Centre for EHR Standards "
    "(NRCeS), Ministry of Health and Family Welfare, Government of India."
)

ABDM_ATTRIBUTION_NOTE = (
    "Ayushman Bharat Digital Mission (ABDM) Drug Registry, National Health Authority, "
    "Government of India."
)

DRUGBANK_ATTRIBUTION_NOTE = (
    "DrugBank Clinical / Drug-Drug Interaction API (requires an active DrugBank license)."
)


def build_source_attribution(
    *,
    source_name: str,
    dataset_version: str | None = None,
    dataset_date: str | None = None,
    url: str | None = None,
    notes: str | None = None,
) -> dict[str, str | None]:
    """Build a standardized source attribution dictionary."""
    return {
        "source_name": source_name,
        "dataset_version": dataset_version,
        "dataset_date": dataset_date,
        "url": url,
        "notes": notes,
    }
