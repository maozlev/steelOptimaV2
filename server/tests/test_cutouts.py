import json
from pathlib import Path

import pytest

PDFS_DIR = Path(__file__).parent.parent.parent / "pdfs"
DOC3 = "Doc_HK3573_290626083217_00 (1).pdf"


@pytest.fixture(scope="module")
def extracted(client):
    import time

    if not (PDFS_DIR / DOC3).exists():
        pytest.skip("sample pdf missing")
    with open(PDFS_DIR / DOC3, "rb") as f:
        r = client.post(
            "/api/documents", files={"file": (DOC3, f, "application/pdf")}
        )
    assert r.status_code == 201
    doc = r.json()
    r = client.post(f"/api/documents/{doc['id']}/jobs")
    assert r.status_code == 202
    job_id = r.json()["id"]
    deadline = time.time() + 300
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("done", "failed"):
            break
        time.sleep(0.2)
    assert job["status"] == "done"
    page_id = client.get(f"/api/documents/{doc['id']}").json()["pages"][0]["id"]
    cutouts = client.get(f"/api/pages/{page_id}/cutouts").json()
    assert cutouts
    return {"doc": doc, "job_id": job_id, "page_id": page_id, "cutouts": cutouts}


def _telemetry_rows(type_: str | None = None) -> list:
    import app.db.session as db_session
    from app.db.models import TelemetryEvent

    with db_session.SessionLocal() as db:
        q = db.query(TelemetryEvent)
        if type_:
            q = q.filter_by(type=type_)
        return q.all()


def test_approve_cutout(client, extracted):
    c = extracted["cutouts"][0]
    r = client.patch(f"/api/cutouts/{c['id']}", json={"action": "approve"})
    assert r.status_code == 200
    assert r.json()["status"] == "approved"
    events = _telemetry_rows("cutout_approved")
    assert any(e.entity_id == c["id"] for e in events)


def test_reject_cutout(client, extracted):
    c = extracted["cutouts"][1]
    r = client.patch(
        f"/api/cutouts/{c['id']}", json={"action": "reject", "session_id": "s1"}
    )
    assert r.status_code == 200
    assert r.json()["status"] == "rejected"
    events = _telemetry_rows("cutout_rejected")
    assert any(e.entity_id == c["id"] and e.session_id == "s1" for e in events)


def test_edit_cutout_preserves_original(client, extracted):
    c = extracted["cutouts"][2]
    new_wkt = "POLYGON ((0 0, 10 0, 10 10, 0 10, 0 0))"
    r = client.patch(
        f"/api/cutouts/{c['id']}",
        json={"action": "edit", "geometry_wkt": new_wkt, "kind": "slot"},
    )
    assert r.status_code == 200
    out = r.json()
    assert out["status"] == "edited"
    assert out["kind"] == "slot"
    assert out["geometry_wkt"] == c["geometry_wkt"]  # original kept for audit
    assert "POLYGON" in out["edited_geometry_wkt"]
    assert out["bbox"] == [0.0, 0.0, 10.0, 10.0]


def test_edit_requires_change(client, extracted):
    c = extracted["cutouts"][3]
    r = client.patch(f"/api/cutouts/{c['id']}", json={"action": "edit"})
    assert r.status_code == 422


def test_edit_rejects_bad_wkt(client, extracted):
    c = extracted["cutouts"][3]
    for bad in ("not wkt", "POINT (1 2)", "POLYGON ((0 0, 1 1, 0 0))"):
        r = client.patch(
            f"/api/cutouts/{c['id']}",
            json={"action": "edit", "geometry_wkt": bad},
        )
        assert r.status_code == 422, bad


def test_patch_unknown_cutout(client):
    r = client.patch("/api/cutouts/999999", json={"action": "approve"})
    assert r.status_code == 404


def test_manual_add(client, extracted):
    page_id = extracted["page_id"]
    r = client.post(
        f"/api/pages/{page_id}/cutouts",
        json={
            "geometry_wkt": "POLYGON ((5 5, 25 5, 25 15, 5 15, 5 5))",
            "kind": "slot",
            "session_id": "s2",
        },
    )
    assert r.status_code == 201
    out = r.json()
    assert out["source"] == "manual"
    assert out["status"] == "approved"
    assert out["job_id"] is None
    assert out["confidence"] == 1.0
    assert out["bbox"] == [5.0, 5.0, 25.0, 15.0]
    events = _telemetry_rows("cutout_added")
    assert any(e.entity_id == out["id"] and e.session_id == "s2" for e in events)
    # manual cutout shows up in the page listing
    listed = client.get(f"/api/pages/{page_id}/cutouts").json()
    assert any(c["id"] == out["id"] for c in listed)


def test_manual_add_unknown_page(client):
    r = client.post(
        "/api/pages/999999/cutouts",
        json={"geometry_wkt": "POLYGON ((0 0, 1 0, 1 1, 0 1, 0 0))", "kind": "hole"},
    )
    assert r.status_code == 404


def test_telemetry_batch(client):
    r = client.post(
        "/api/telemetry/events",
        json={
            "session_id": "ui-1",
            "events": [
                {"type": "page_viewed", "entity_id": 1},
                {"type": "overlay_toggled", "payload": {"on": True}},
            ],
        },
    )
    assert r.status_code == 202
    assert r.json()["accepted"] == 2
    rows = _telemetry_rows("overlay_toggled")
    assert rows and rows[-1].session_id == "ui-1"
    assert json.loads(rows[-1].payload_json) == {"on": True}


def test_telemetry_batch_empty_rejected(client):
    r = client.post("/api/telemetry/events", json={"events": []})
    assert r.status_code == 422


def test_pipeline_emits_telemetry(client, extracted):
    job_id = extracted["job_id"]
    started = _telemetry_rows("job_started")
    assert any(e.entity_id == job_id for e in started)
    done = _telemetry_rows("job_done")
    assert any(e.entity_id == job_id for e in done)
    pages = _telemetry_rows("page_done")
    assert any(
        json.loads(e.payload_json)["job_id"] == job_id for e in pages
    )
