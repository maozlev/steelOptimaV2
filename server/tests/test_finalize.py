import json
from pathlib import Path

import pytest

PDFS_DIR = Path(__file__).parent.parent.parent / "pdfs"


def _upload_pdf(client, name_prefix=""):
    pdf = next(iter(sorted(PDFS_DIR.glob("*.pdf"))))
    r = client.post(
        "/api/documents",
        files={
            "file": (f"{name_prefix}{pdf.name}", pdf.read_bytes(), "application/pdf")
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _add_cutout_row(page_id: int, confidence: float, status: str = "pending") -> int:
    from app.db import session as db_session
    from app.db.models import Cutout

    with db_session.SessionLocal() as db:
        c = Cutout(
            page_id=page_id,
            job_id=None,
            geometry_wkt="POLYGON ((10 10, 20 10, 20 20, 10 20, 10 10))",
            bbox=json.dumps([10, 10, 20, 20]),
            kind="hole",
            source="vector",
            confidence=confidence,
            status=status,
        )
        db.add(c)
        db.commit()
        return c.id


@pytest.fixture(scope="module")
def finalized_doc(client):
    doc = _upload_pdf(client)
    page_id = doc["pages"][0]["id"]
    ids = {
        "high_pending": _add_cutout_row(page_id, 0.95),
        "low_pending": _add_cutout_row(page_id, 0.85),
        "pre_approved": _add_cutout_row(page_id, 0.5, status="approved"),
        "pre_rejected": _add_cutout_row(page_id, 0.95, status="rejected"),
    }
    # The scale is the operator's to set, and finalize is blocked until they do — every
    # dimension is a paper measurement multiplied by it.
    assert client.patch(f"/api/pages/{page_id}/scale", json={"scale": 1.0}).status_code == 200
    r = client.post(f"/api/documents/{doc['id']}/finalize", json={})
    assert r.status_code == 200, r.text
    return doc, page_id, ids, r.json()


def test_finalize_refuses_an_unconfirmed_scale(client):
    """Nothing is cut from a size nobody signed off on.

    Every dimension in the BOM is a paper measurement multiplied by the sheet scale. Maoz
    made that number the operator's responsibility — so the operator has to actually
    supply it, and the export cannot slip out with it unset.
    """
    # a DIFFERENT pdf — upload dedupes on content hash, not filename
    other = sorted(PDFS_DIR.glob("*.pdf"))[1]
    r = client.post(
        "/api/documents",
        files={"file": (other.name, other.read_bytes(), "application/pdf")},
    )
    assert r.status_code == 201, r.text
    doc = r.json()
    _add_cutout_row(doc["pages"][0]["id"], 0.95)

    r = client.post(f"/api/documents/{doc['id']}/finalize", json={})
    assert r.status_code == 409
    assert "scale" in r.json()["detail"].lower()

    client.patch(f"/api/pages/{doc['pages'][0]['id']}/scale", json={"scale": 5.0})
    assert client.post(f"/api/documents/{doc['id']}/finalize", json={}).status_code == 200


def test_config_endpoint(client):
    r = client.get("/api/config")
    assert r.status_code == 200
    body = r.json()
    assert body["finalize_threshold"] == 0.90
    assert body["escalation_threshold"] == 0.65


def test_finalize_thresholds(client, finalized_doc):
    doc, page_id, ids, out = finalized_doc
    assert out["auto_approved"] == 1
    assert out["auto_rejected"] == 1
    assert out["already_reviewed"] == 2
    assert out["document"]["status"] == "approved"

    statuses = {
        c["id"]: c["status"]
        for c in client.get(f"/api/pages/{page_id}/cutouts").json()
    }
    assert statuses[ids["high_pending"]] == "approved"
    assert statuses[ids["low_pending"]] == "rejected"
    assert statuses[ids["pre_approved"]] == "approved"
    assert statuses[ids["pre_rejected"]] == "rejected"

    assert client.get(f"/api/documents/{doc['id']}").json()["status"] == "approved"


def test_finalize_twice_409(client, finalized_doc):
    doc = finalized_doc[0]
    assert client.post(f"/api/documents/{doc['id']}/finalize", json={}).status_code == 409


def test_locked_patch_409(client, finalized_doc):
    ids = finalized_doc[2]
    r = client.patch(
        f"/api/cutouts/{ids['high_pending']}", json={"action": "reject"}
    )
    assert r.status_code == 409


def test_locked_add_cutout_409(client, finalized_doc):
    page_id = finalized_doc[1]
    r = client.post(
        f"/api/pages/{page_id}/cutouts",
        json={
            "geometry_wkt": "POLYGON ((0 0, 5 0, 5 5, 0 5, 0 0))",
            "kind": "hole",
        },
    )
    assert r.status_code == 409


def test_locked_job_409(client, finalized_doc):
    doc = finalized_doc[0]
    assert client.post(f"/api/documents/{doc['id']}/jobs").status_code == 409


def test_locked_crop_409(client, finalized_doc):
    doc = finalized_doc[0]
    r = client.post(
        f"/api/documents/{doc['id']}/crop",
        json={"x_min": 0.1, "y_min": 0.1, "x_max": 0.9, "y_max": 0.9},
    )
    assert r.status_code == 409


def test_export_allowed_when_locked(client, finalized_doc):
    doc = finalized_doc[0]
    r = client.get(f"/api/documents/{doc['id']}/export")
    assert r.status_code == 200
