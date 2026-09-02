"""The fee routes, priced from the committed fixture. No database here."""

from __future__ import annotations

from typing import TYPE_CHECKING

from worth_fees import PlaceOfService, expected_allowed

if TYPE_CHECKING:
    from fastapi.testclient import TestClient


def test_health_says_prices_come_from_the_fixture_without_a_database(client: TestClient) -> None:
    body = client.get("/api/health").json()
    assert body["database"] == "not configured"
    assert [v["label"] for v in body["fees"]] == ["RVU26A", "RVU26B", "RVU26C", "RVU26D"]
    assert all(v["origin"] == "fixture" and v["coverage"] == "fixture" for v in body["fees"])


def test_a_price_matches_the_package_and_carries_its_receipts(client: TestClient) -> None:
    body = client.post(
        "/api/fees/price",
        json={"code": "99213", "locality": "CA18", "date": "2026-03-14"},
    ).json()
    direct = expected_allowed("99213", [], "CA18", PlaceOfService.NON_FACILITY, 2026, 1)
    assert body["amount"] == str(direct.amount)
    assert body["trace"] == list(direct.trace)
    assert body["ruleYear"] == 2026 and body["quarter"] == 1
    assert body["placeOfService"] == "non-facility"
    assert body["inputs"]["conversionFactor"] == str(direct.conversion_factor)
    roles = [f["role"] for f in body["source"]["files"]]
    assert "archive" in roles and any(len(f["sha256"]) == 64 for f in body["source"]["files"])


def test_the_facility_setting_and_the_other_basis_change_the_answer(client: TestClient) -> None:
    base = client.post(
        "/api/fees/price", json={"code": "99213", "locality": "CA18", "date": "2026Q1"}
    )
    facility = client.post(
        "/api/fees/price",
        json={"code": "99213", "locality": "CA18", "date": "2026Q1", "setting": "facility"},
    )
    qpp = client.post(
        "/api/fees/price",
        json={
            "code": "99213",
            "locality": "CA18",
            "date": "2026Q1",
            "paymentBasis": "qualifying-apm",
        },
    )
    amounts = {r.json()["amount"] for r in (base, facility, qpp)}
    assert len(amounts) == 3


def test_a_refusal_is_a_422_with_the_package_s_own_reason(client: TestClient) -> None:
    unknown = client.post(
        "/api/fees/price", json={"code": "00000", "locality": "CA18", "date": "2026"}
    )
    assert unknown.status_code == 422
    assert unknown.json()["kind"] == "UnknownCodeError"

    scaled = client.post(
        "/api/fees/price",
        json={"code": "99213", "locality": "CA18", "date": "2026", "modifiers": ["50"]},
    )
    assert scaled.status_code == 422
    assert "modifier 50" in scaled.json()["detail"]

    unpinned = client.post(
        "/api/fees/price", json={"code": "99213", "locality": "CA18", "date": "2024-05-01"}
    )
    assert unpinned.status_code == 422
    assert unpinned.json()["kind"] == "VintageError"


def test_localities_for_a_date(client: TestClient) -> None:
    body = client.get("/api/fees/localities", params={"date": "2026-07-01"}).json()
    assert [entry["locality"] for entry in body] == ["AL00", "CA18", "NY01"]
    assert body[2]["name"] == "MANHATTAN"


def test_vintages_are_listed_with_what_they_govern(client: TestClient) -> None:
    body = client.get("/api/fees/vintages").json()
    assert body[0]["effectiveFrom"] == "2026-01-01" and body[0]["effectiveTo"] == "2026-04-01"
    assert body[0]["codes"] > 0 and body[0]["localities"] == 3
