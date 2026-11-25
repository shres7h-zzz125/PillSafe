"""Unit and integration tests for PillSafe API endpoints, caching, OCR, and safety rules."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.services.ocr_normalizer import normalize_ocr_text


def _make_settings(tmp_path: Path, **overrides: object) -> Settings:
    cdci_dir = tmp_path / "cdci"
    cdci_dir.mkdir(parents=True, exist_ok=True)
    defaults: dict[str, object] = {
        "cache_db_path": str(tmp_path / "cache.db"),
        "cdci_data_dir": str(cdci_dir),
        "abdm_client_id": "",
        "abdm_client_secret": "",
        "drugbank_api_key": "",
        "openfda_api_key": "",
    }
    defaults.update(overrides)
    return Settings(**defaults)


def _mock_transport(call_counts: dict[str, int] | None = None, *, rxnorm_429: bool = False) -> httpx.MockTransport:
    counts = call_counts if call_counts is not None else {}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        counts[url] = counts.get(url, 0) + 1

        if "rxnav.nlm.nih.gov" in url:
            if rxnorm_429 and "/version.json" not in url:
                return httpx.Response(429, json={"error": "rate limit"})
            if url.endswith("/version.json"):
                return httpx.Response(200, json={"version": "2026-11-01", "apiVersion": "3.1"})
            if "/drugs.json" in url:
                return httpx.Response(
                    200,
                    json={
                        "drugGroup": {
                            "conceptGroup": [
                                {
                                    "tty": "SCD",
                                    "conceptProperties": [
                                        {"rxcui": "900001", "name": "TestSubstanceA 500 MG Oral Tablet"}
                                    ],
                                }
                            ]
                        }
                    },
                )
            if "/approximateTerm.json" in url:
                return httpx.Response(
                    200,
                    json={
                        "approximateGroup": {
                            "candidate": [
                                {"rxcui": "900001", "score": "92"},
                            ]
                        }
                    },
                )
            if "/rxcui/900001/properties.json" in url:
                return httpx.Response(
                    200,
                    json={
                        "properties": {
                            "rxcui": "900001",
                            "name": "TestSubstanceA 500 MG Oral Tablet",
                            "tty": "SCD",
                        }
                    },
                )
            if "/rxcui/900002/properties.json" in url:
                return httpx.Response(
                    200,
                    json={
                        "properties": {
                            "rxcui": "900002",
                            "name": "TestSubstanceA 650 MG Oral Tablet",
                            "tty": "SCD",
                        }
                    },
                )
            if "/rxcui/900003/properties.json" in url:
                return httpx.Response(
                    200,
                    json={
                        "properties": {
                            "rxcui": "900003",
                            "name": "TestSubstanceB 10 MG Oral Tablet",
                            "tty": "SCD",
                        }
                    },
                )
            if "/rxcui/900001/related.json" in url or "/rxcui/900002/related.json" in url:
                return httpx.Response(
                    200,
                    json={
                        "relatedGroup": {
                            "conceptGroup": [
                                {
                                    "tty": "IN",
                                    "conceptProperties": [
                                        {"rxcui": "1001", "name": "TestSubstanceA"}
                                    ],
                                }
                            ]
                        }
                    },
                )
            if "/rxcui/900003/related.json" in url:
                return httpx.Response(
                    200,
                    json={
                        "relatedGroup": {
                            "conceptGroup": [
                                {
                                    "tty": "IN",
                                    "conceptProperties": [
                                        {"rxcui": "1002", "name": "TestSubstanceB"}
                                    ],
                                }
                            ]
                        }
                    },
                )
            if "/allProperties.json" in url:
                return httpx.Response(
                    200,
                    json={"propConceptGroup": {"propConcept": [{"propName": "RxNorm Name", "propValue": "TestSubstanceA"}]}},
                )
            if "/properties.json" in url:
                return httpx.Response(200, json={})

        if "api.fda.gov" in url:
            if "900001" in url or "TestSubstanceA" in url:
                return httpx.Response(
                    200,
                    json={
                        "meta": {"last_updated": "2026-10-15"},
                        "results": [
                            {
                                "openfda": {"rxcui": ["900001"], "substance_name": ["TESTSUBSTANCEA"]},
                                "warnings": ["Sample FDA label warning text."],
                                "boxed_warning": [],
                                "contraindications": ["Sample contraindication text."],
                                "dosage_and_administration": ["Sample label dosage text."],
                                "drug_interactions": ["Sample FDA label interaction reference text."],
                            }
                        ],
                    },
                )
            return httpx.Response(404, json={"error": {"code": "NOT_FOUND", "message": "No matches found!"}})

        return httpx.Response(404, json={"error": "unmocked url"})

    return httpx.MockTransport(handler)


def test_ocr_normalizer_strips_packaging_noise() -> None:
    raw = """
    B.No. AB1234 Mfg Date: 01/2026 Exp Date: 12/2028
    MRP Rs. 45.00 Incl. of all taxes
    TestBrand 500mg + TestCoAgent 650mg Tablets IP
    Keep out of reach of children
    """
    result = normalize_ocr_text(raw, is_ocr=True)
    normalized = str(result["normalized_query"])
    assert "AB1234" not in normalized
    assert "45.00" not in normalized
    assert "500 mg" in normalized
    assert "650 mg" in normalized
    assert any("TestBrand" in token for token in result["search_terms"])  # type: ignore[operator]


def test_health_reports_sources_and_cache(tmp_path: Path) -> None:
    app = create_app(_make_settings(tmp_path), transport=_mock_transport())
    client = TestClient(app)

    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "disclaimer" in data
    source_names = {src["source_name"]: src for src in data["sources"]}
    assert source_names["NLM RxNorm (RxNav REST API)"]["available"] is True
    assert source_names["NLM RxNorm (RxNav REST API)"]["dataset_version"] == "2026-11-01"
    assert source_names["DrugBank Drug-Drug Interaction API"]["available"] is False
    assert source_names["NRCeS Common Drug Codes for India (CDCI)"]["available"] is False
    assert source_names["ABDM Drug Registry"]["available"] is False


def test_search_requires_confirmation_and_caches_results(tmp_path: Path) -> None:
    counts: dict[str, int] = {}
    app = create_app(_make_settings(tmp_path), transport=_mock_transport(counts))
    client = TestClient(app)

    resp1 = client.get("/api/v1/medicines/search", params={"q": "TestSubstanceA 500mg Tablets IP", "is_ocr": True})
    assert resp1.status_code == 200
    body = resp1.json()
    assert body["total"] >= 1
    first = body["results"][0]
    assert first["id"] == "rxcui:900001"
    assert first["requires_confirmation"] is True
    assert first["source_attribution"]["dataset_version"] == "2026-11-01"
    assert first["active_ingredients"][0]["name"] == "TestSubstanceA"

    calls_after_first = sum(counts.values())
    resp2 = client.get("/api/v1/medicines/search", params={"q": "TestSubstanceA 500mg Tablets IP", "is_ocr": True})
    assert resp2.status_code == 200
    assert sum(counts.values()) == calls_after_first


def test_search_handles_rxnorm_rate_limit_gracefully(tmp_path: Path) -> None:
    app = create_app(_make_settings(tmp_path), transport=_mock_transport(rxnorm_429=True))
    client = TestClient(app)

    resp = client.get("/api/v1/medicines/search", params={"q": "TestSubstanceA"})
    assert resp.status_code == 200
    statuses = {s["source_name"]: s for s in resp.json()["source_statuses"]}
    assert statuses["NLM RxNorm (RxNav REST API)"]["available"] is False
    assert "429" in str(statuses["NLM RxNorm (RxNav REST API)"]["message"])


def test_cdci_local_tsv_loading_and_search(tmp_path: Path) -> None:
    cdci_dir = tmp_path / "cdci"
    cdci_dir.mkdir(parents=True, exist_ok=True)
    (cdci_dir / "release_info.json").write_text(
        json.dumps({"version": "CDCI-2026.01", "release_date": "2026-01-15"}),
        encoding="utf-8",
    )
    (cdci_dir / "branded_medicines.tsv").write_text(
        "cdci_code\tbrand_name\tgeneric_name\tsubstance_name\tdosage_form\tstrength\n"
        "CDCI-001\tSampleIndianBrand 500\tTestSubstanceA 500 mg Tablet\tTestSubstanceA\tTablet\t500 mg\n",
        encoding="utf-8",
    )

    app = create_app(_make_settings(tmp_path, cdci_data_dir=str(cdci_dir)), transport=_mock_transport())
    client = TestClient(app)

    resp = client.get("/api/v1/medicines/search", params={"q": "SampleIndianBrand", "is_ocr": True})
    assert resp.status_code == 200
    results = resp.json()["results"]
    assert any(item["id"] == "cdci:CDCI-001" for item in results)
    cdci_item = next(item for item in results if item["id"] == "cdci:CDCI-001")
    assert cdci_item["source_attribution"]["dataset_version"] == "CDCI-2026.01"
    assert cdci_item["source_attribution"]["dataset_date"] == "2026-01-15"


def test_medicine_detail_and_not_found(tmp_path: Path) -> None:
    app = create_app(_make_settings(tmp_path), transport=_mock_transport())
    client = TestClient(app)

    ok_resp = client.get("/api/v1/medicines/rxcui:900001")
    assert ok_resp.status_code == 200
    detail = ok_resp.json()
    assert detail["id"] == "rxcui:900001"
    assert detail["fda_label"] is not None
    assert detail["fda_label"]["source_attribution"]["dataset_date"] == "2026-10-15"

    missing_resp = client.get("/api/v1/medicines/rxcui:9999999")
    assert missing_resp.status_code == 404


def test_confirmation_workflow_and_list_management(tmp_path: Path) -> None:
    app = create_app(_make_settings(tmp_path), transport=_mock_transport())
    client = TestClient(app)

    reject_resp = client.post(
        "/api/v1/medicines/confirm",
        json={"medicine_id": "rxcui:900001", "confirmed": False},
    )
    assert reject_resp.status_code == 400

    confirm_resp = client.post(
        "/api/v1/medicines/confirm",
        json={"medicine_id": "rxcui:900001", "confirmed": True, "user_dosage_note": "Morning dose"},
    )
    assert confirm_resp.status_code == 201
    assert confirm_resp.json()["id"] == "rxcui:900001"

    list_resp = client.get("/api/v1/medicine-list")
    assert list_resp.status_code == 200
    assert list_resp.json()["total"] == 1

    del_resp = client.delete("/api/v1/medicine-list/rxcui:900001")
    assert del_resp.status_code == 200
    assert client.get("/api/v1/medicine-list").json()["total"] == 0


def test_check_requires_confirmation_returns_grey_and_detects_duplicates(tmp_path: Path) -> None:
    app = create_app(_make_settings(tmp_path), transport=_mock_transport())
    client = TestClient(app)

    # Unconfirmed items must be rejected
    unconfirmed_resp = client.post(
        "/api/v1/check",
        json={
            "medicines": [
                {"medicine_id": "rxcui:900001", "confirmed": False},
                {"medicine_id": "rxcui:900003", "confirmed": True},
            ]
        },
    )
    assert unconfirmed_resp.status_code == 400

    # Two distinct active ingredients without licensed DDI provider -> grey "Could not check"
    grey_resp = client.post(
        "/api/v1/check",
        json={
            "medicines": [
                {"medicine_id": "rxcui:900001", "confirmed": True},
                {"medicine_id": "rxcui:900003", "confirmed": True},
            ]
        },
    )
    assert grey_resp.status_code == 200
    grey_body = grey_resp.json()
    assert grey_body["overall_status"] == "Could not check"
    assert grey_body["overall_status_code"] == "could_not_check"
    assert grey_body["badge_color"] == "grey"
    assert grey_body["pair_results"][0]["status"] == "Could not check"
    assert grey_body["pair_results"][0]["badge_color"] == "grey"
    assert "safe" not in grey_body["overall_status"].lower()

    # Two medicines sharing the same active ingredient -> duplicate ingredient alert + grey interaction
    dup_resp = client.post(
        "/api/v1/check",
        json={
            "medicines": [
                {"medicine_id": "rxcui:900001", "confirmed": True},
                {"medicine_id": "rxcui:900002", "confirmed": True},
            ]
        },
    )
    assert dup_resp.status_code == 200
    dup_body = dup_resp.json()
    assert len(dup_body["duplicate_ingredients"]) == 1
    assert dup_body["duplicate_ingredients"][0]["normalized_ingredient"] == "testsubstancea"
    assert dup_body["pair_results"][0]["status"] == "Could not check"
    assert dup_body["pair_results"][0]["badge_color"] == "grey"
