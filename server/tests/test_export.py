from pathlib import Path

import pytest

PDFS_DIR = Path(__file__).parent.parent.parent / "pdfs"
DOC3 = "Doc_HK3573_290626083217_00 (1).pdf"


@pytest.fixture(scope="module")
def reviewed(client):
    """Extract Doc3, then approve/reject/edit a few cutouts."""
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
    assert len(cutouts) >= 4

    approved = cutouts[0]
    rejected = cutouts[1]
    edited = cutouts[2]
    client.patch(f"/api/cutouts/{approved['id']}", json={"action": "approve"})
    client.patch(f"/api/cutouts/{rejected['id']}", json={"action": "reject"})
    client.patch(
        f"/api/cutouts/{edited['id']}",
        json={
            "action": "edit",
            "geometry_wkt": "POLYGON ((0 0, 20 0, 20 10, 0 10, 0 0))",
        },
    )
    manual = client.post(
        f"/api/pages/{page_id}/cutouts",
        json={
            "geometry_wkt": "POLYGON ((30 30, 40 30, 40 40, 30 40, 30 30))",
            "kind": "hole",
        },
    ).json()
    return {
        "doc": doc,
        "page_id": page_id,
        "approved": approved,
        "rejected": rejected,
        "edited": edited,
        "manual": manual,
    }


def test_export_only_accepted(client, reviewed):
    r = client.get(f"/api/documents/{reviewed['doc']['id']}/export")
    assert r.status_code == 200
    out = r.json()
    assert out["units"] == "mm"
    assert out["cutout_count"] == 3  # approved + edited + manual
    ids = {c["id"] for p in out["pages"] for c in p["cutouts"]}
    assert ids == {
        reviewed["approved"]["id"],
        reviewed["edited"]["id"],
        reviewed["manual"]["id"],
    }


def test_export_uses_edited_geometry(client, reviewed):
    out = client.get(f"/api/documents/{reviewed['doc']['id']}/export").json()
    by_id = {c["id"]: c for p in out["pages"] for c in p["cutouts"]}
    edited = by_id[reviewed["edited"]["id"]]
    # 20pt x 10pt rectangle in mm
    xs = [p[0] for p in edited["points_mm"]]
    ys = [p[1] for p in edited["points_mm"]]
    assert max(xs) == pytest.approx(20 * 25.4 / 72, abs=0.01)
    assert max(ys) == pytest.approx(10 * 25.4 / 72, abs=0.01)
    assert edited["geometry_wkt_pt"] != reviewed["edited"]["geometry_wkt"]

    approved = by_id[reviewed["approved"]["id"]]
    assert approved["geometry_wkt_pt"] == reviewed["approved"]["geometry_wkt"]
    assert approved["dims"] is not None
    assert len(approved["points_mm"]) >= 4


def test_export_page_metadata(client, reviewed):
    out = client.get(f"/api/documents/{reviewed['doc']['id']}/export").json()
    assert out["document"]["filename"] == DOC3
    page = out["pages"][0]
    assert page["width_mm"] > 0 and page["height_mm"] > 0
    assert out["coordinate_system"] == "page_top_left_y_down"


def test_export_unknown_document(client):
    assert client.get("/api/documents/999999/export").status_code == 404


def test_export_emits_telemetry(client, reviewed):
    import app.db.session as db_session
    from app.db.models import TelemetryEvent

    client.get(f"/api/documents/{reviewed['doc']['id']}/export")
    with db_session.SessionLocal() as db:
        rows = db.query(TelemetryEvent).filter_by(type="document_exported").all()
        assert any(e.entity_id == reviewed["doc"]["id"] for e in rows)


def test_telemetry_summary(client, reviewed):
    r = client.get("/api/telemetry/summary")
    assert r.status_code == 200
    out = r.json()
    assert out["escalation_threshold"] == 0.65

    manual = out["by_source"]["manual"]
    assert manual["approved"] >= 1
    assert manual["approve_rate"] == 1.0

    vector = out["by_source"]["vector"]
    assert vector["reviewed"] >= 3
    assert vector["rejected"] >= 1
    assert 0.0 < vector["approve_rate"] < 1.0

    assert len(out["by_confidence"]) == 5
    assert sum(b["pending"] + b["reviewed"] for b in out["by_confidence"]) > 0
    for bucket in out["by_confidence"]:
        assert bucket["reviewed"] == (
            bucket["approved"] + bucket["rejected"] + bucket["edited"]
        )

    assert out["vlm"]["calls"] == 0  # vlm disabled in tests
    assert out["vlm"]["ok_rate"] is None
