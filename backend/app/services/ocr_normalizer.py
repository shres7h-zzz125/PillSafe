"""OCR text normalization for medicine strip scans."""

from __future__ import annotations

import re

# Common Indian & international packaging boilerplate to strip from OCR text
_BOILERPLATE_PATTERNS = [
    r"\b(?:b\.?\s*no\.?|batch\s*no\.?|lot\s*no\.?)\s*[:\-]?\s*[a-z0-9\-/]+\b",
    r"\b(?:mfg\.?|mfd\.?|exp\.?|expiry)\s*(?:date|dt\.?)?\s*[:\-]?\s*[a-z0-9/\-\.]+\b",
    r"\b(?:m\.?r\.?p\.?|rs\.?|inr)\s*[:\-]?\s*[\d\.,]+\b",
    r"\bincl\.?\s*of\s*all\s*taxes\b",
    r"\bschedule\s*[a-z0-9]+\s*(?:prescription\s*)?drug\b",
    r"\bkeep\s+out\s+of\s+reach\s+of\s+children\b",
    r"\bstore\s+in\s+a\s+cool\s*(?:,|and)?\s*dry\s+place\b",
    r"\bfor\s+oral\s+use\s+only\b",
    r"\b(?:tablets?|capsules?|syrup|suspension|injection)\s+(?:i\.?p\.?|b\.?p\.?|u\.?s\.?p\.?)\b",
    r"\b(?:i\.?p\.?|b\.?p\.?|u\.?s\.?p\.?)\b",
    r"\beach\s+(?:film[- ]coated\s+|uncoated\s+)?(?:tablet|capsule)\s+contains\b",
    r"\bmanufactured\s+by\b",
    r"\bmarketed\s+by\b",
    r"\bregd\.?\s*trade\s*mark\b",
]

_UNIT_PATTERN = re.compile(r"\b(\d+(?:\.\d+)?)\s*(mg|mcg|g|gm|ml|iu|%)\b", re.IGNORECASE)
_NOISE_CHARS = re.compile(r"[_~`|•●■◆*#@]+")
_MULTISPACE = re.compile(r"\s+")


def normalize_ocr_text(raw_text: str, *, is_ocr: bool = True) -> dict[str, object]:
    """Clean OCR-extracted medicine strip text into normalized search terms and tokens.

    Preserves drug names and strength tokens while removing packaging boilerplate.
    """
    cleaned = raw_text.strip()
    if not cleaned:
        return {"normalized_query": "", "search_terms": [], "extracted_tokens": []}

    cleaned = _NOISE_CHARS.sub(" ", cleaned)
    cleaned = cleaned.replace("\r", "\n")

    if is_ocr:
        for pattern in _BOILERPLATE_PATTERNS:
            cleaned = re.sub(pattern, " ", cleaned, flags=re.IGNORECASE)

    # Normalize dosage units like 500MG -> 500 mg
    cleaned = _UNIT_PATTERN.sub(lambda m: f"{m.group(1)} {m.group(2).lower()}", cleaned)

    lines = [
        _MULTISPACE.sub(" ", line).strip(" ,;:-.")
        for line in cleaned.split("\n")
    ]
    lines = [line for line in lines if line and len(line) >= 2]

    normalized_query = _MULTISPACE.sub(" ", " ".join(lines)).strip()

    # Build candidate search terms: full query, individual lines, and split combination parts
    search_terms: list[str] = []
    seen: set[str] = set()

    def _add_term(term: str) -> None:
        t = _MULTISPACE.sub(" ", term).strip(" ,;:-.")
        if len(t) < 2:
            return
        # Skip pure numeric or pure unit tokens
        if re.fullmatch(r"[\d\.\s]+(?:mg|mcg|g|gm|ml|iu|%)?", t, re.IGNORECASE):
            return
        key = t.lower()
        if key not in seen:
            seen.add(key)
            search_terms.append(t)

    _add_term(normalized_query)
    for line in lines:
        _add_term(line)
        # Split multi-ingredient combinations such as "A + B" or "A / B"
        for part in re.split(r"\s*[+/&]\s*", line):
            _add_term(part)
            # Also try the part without trailing strength numbers for generic RxNorm matching
            without_strength = _UNIT_PATTERN.sub("", part).strip()
            _add_term(without_strength)

    # Also add version of full query without numeric strengths
    query_without_strength = _UNIT_PATTERN.sub("", normalized_query).strip()
    _add_term(query_without_strength)

    return {
        "normalized_query": normalized_query,
        "search_terms": search_terms[:8],
        "extracted_tokens": search_terms,
    }
