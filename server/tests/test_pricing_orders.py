"""Pricing + orders on top of an approved NCD table."""

from pathlib import Path

import pytest

TABLES_DIR = Path(__file__).parent.parent.parent / "tables"


def _wait(client, job_id: int, timeout: float = 300.0) -> dict:
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("done", "failed"):
            return job
        time.sleep(0.2)
    raise TimeoutError


@pytest.fixture(scope="module")
def approved_project(client):
    pdf = TABLES_DIR / "NCD5168[_EN](5).pdf"
    project_id = client.post("/api/projects", json={"name": "Pricing"}).json()["id"]
    with open(pdf, "rb") as f:
        doc = client.post(
            f"/api/projects/{project_id}/documents",
            files={"file": (pdf.name, f, "application/pdf")},
        ).json()
    r = client.post(f"/api/documents/{doc['id']}/table-jobs", json={"vlm": False})
    assert _wait(client, r.json()["id"])["status"] == "done"
    tables = client.get(f"/api/documents/{doc['id']}/tables").json()
    bom = max(tables, key=lambda t: t["row_count"])
    for row in client.get(f"/api/tables/{bom['id']}").json()["rows"]:
        if row["status"] == "needs_review":
            client.patch(f"/api/material-rows/{row['id']}", json={"action": "approve"})
    assert (
        client.patch(f"/api/tables/{bom['id']}", json={"action": "approve"}).status_code
        == 200
    )
    return project_id


def test_bid_flow(client, approved_project):
    project_id = approved_project
    bid = client.get(f"/api/projects/{project_id}/bid").json()
    assert bid["total"] == 0
    assert "L160X160X15" in bid["unpriced_keys"]

    # price the legs per kg, the L60 angles per meter
    r = client.put(
        f"/api/projects/{project_id}/prices",
        json={
            "entries": [
                {"material_key": "L160X160X15", "price": 5.0, "pricing_unit": "per_kg"},
                {"material_key": "L60X60X6", "price": 12.0, "pricing_unit": "per_m"},
            ]
        },
    )
    assert r.status_code == 200 and r.json()["written"] == 2

    bid = client.get(f"/api/projects/{project_id}/bid").json()
    legs = next(r for r in bid["rows"] if r["material_key"] == "L160X160X15")
    # 2 rows x 651.6 kg = 1303.2 kg x 5.0
    assert legs["line_total"] == pytest.approx(1303.2 * 5.0)
    angles = next(r for r in bid["rows"] if r["material_key"] == "L60X60X6")
    assert angles["pricing_unit"] == "per_m"
    assert angles["line_total"] == pytest.approx(
        angles["total_length_mm"] / 1000 * 12.0, abs=0.01
    )
    assert bid["total"] == pytest.approx(
        sum(r["line_total"] or 0 for r in bid["rows"])
    )
    assert "L160X160X15" not in bid["unpriced_keys"]

    # unpriced lines are named, never silently zeroed
    assert all(
        r["line_total"] is None
        for r in bid["rows"]
        if r["material_key"] in bid["unpriced_keys"]
    )


def test_order_plan_from_summary(client, approved_project):
    project_id = approved_project
    r = client.post(
        f"/api/projects/{project_id}/order-plans",
        json={
            "material_key": "L60X60X6",
            "stock": [{"length_mm": 12000, "price": 130.0}],
            "kerf_mm": 5,
        },
    )
    assert r.status_code == 201, r.text
    result = r.json()["result"]
    assert result["order"][0]["stock_length_mm"] == 12000
    assert result["total_cost"] == sum(o["subtotal"] for o in result["order"])
    assert result["infeasible_lengths_mm"] == []

    listed = client.get(f"/api/projects/{project_id}/order-plans").json()
    assert listed and listed[0]["result"]["total_cost"] == result["total_cost"]


def test_order_plan_explicit_pieces(client, approved_project):
    r = client.post(
        f"/api/projects/{approved_project}/order-plans",
        json={
            "pieces": [{"length_mm": 13000, "qty": 10}],
            "stock": [{"length_mm": 15000, "price": 100.0}],
        },
    )
    assert r.status_code == 201
    result = r.json()["result"]
    # the canonical no-splicing answer: 10 bars, not 9
    assert result["order"] == [
        {"stock_length_mm": 15000, "count": 10, "unit_price": 100.0, "subtotal": 1000.0}
    ]
