"""Moving documents into projects — the adoption path for pre-hierarchy orphans."""

from pathlib import Path

PDFS_DIR = Path(__file__).parent.parent.parent / "pdfs"


def _upload_orphan(client):
    pdf = PDFS_DIR / "333-532-294_2_BLANK.pdf"
    with open(pdf, "rb") as f:
        r = client.post(
            "/api/documents", files={"file": (pdf.name, f, "application/pdf")}
        )
    if r.status_code == 409:
        docs = client.get("/api/documents").json()
        return next(d for d in docs if d["filename"] == pdf.name)
    assert r.status_code == 201, r.text
    return r.json()


def test_move_document_into_project(client):
    doc = _upload_orphan(client)
    project_id = client.post("/api/projects", json={"name": "adopter"}).json()["id"]

    r = client.patch(f"/api/documents/{doc['id']}", json={"project_id": project_id})
    assert r.status_code == 200
    assert r.json()["project_id"] == project_id

    # the document now shows up under its project
    detail = client.get(f"/api/projects/{project_id}").json()
    assert any(d["id"] == doc["id"] for d in detail["documents"])
    # and the flat list carries the assignment for the orphan filter
    listed = next(
        d for d in client.get("/api/documents").json() if d["id"] == doc["id"]
    )
    assert listed["project_id"] == project_id


def test_move_document_validates_target(client):
    doc = _upload_orphan(client)
    assert (
        client.patch(f"/api/documents/{doc['id']}", json={"project_id": 99999})
    ).status_code == 404
    assert (
        client.patch("/api/documents/99999", json={"project_id": 1})
    ).status_code == 404
