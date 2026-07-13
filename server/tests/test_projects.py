from pathlib import Path

TABLES_DIR = Path(__file__).parent.parent.parent / "tables"


def _upload(client, project_id: int, pdf_path):
    with open(pdf_path, "rb") as f:
        return client.post(
            f"/api/projects/{project_id}/documents",
            files={"file": (pdf_path.name, f, "application/pdf")},
        )


def test_project_crud(client):
    r = client.post("/api/projects", json={"name": "Bid 833", "note": "tender"})
    assert r.status_code == 201
    project = r.json()
    assert project["name"] == "Bid 833"

    r = client.get("/api/projects")
    assert r.status_code == 200
    listed = [p for p in r.json() if p["id"] == project["id"]]
    assert listed and listed[0]["document_count"] == 0

    r = client.patch(f"/api/projects/{project['id']}", json={"name": "Bid 833.1"})
    assert r.status_code == 200
    assert r.json()["name"] == "Bid 833.1"

    r = client.post("/api/projects", json={"name": "   "})
    assert r.status_code == 422

    r = client.delete(f"/api/projects/{project['id']}")
    assert r.status_code == 204
    assert client.get(f"/api/projects/{project['id']}").status_code == 404


def test_upload_document_into_project(client):
    project_id = client.post("/api/projects", json={"name": "Upload test"}).json()["id"]
    pdf = TABLES_DIR / "NCD5168[_EN](5).pdf"

    r = _upload(client, project_id, pdf)
    assert r.status_code == 201
    doc = r.json()

    detail = client.get(f"/api/projects/{project_id}").json()
    assert [d["id"] for d in detail["documents"]] == [doc["id"]]

    # duplicate into the same project → 409 (already attached)
    r = _upload(client, project_id, pdf)
    assert r.status_code == 409


def test_duplicate_orphan_is_adopted(client):
    pdf = TABLES_DIR / "833.1-01-20.pdf"
    with open(pdf, "rb") as f:
        r = client.post(
            "/api/documents", files={"file": (pdf.name, f, "application/pdf")}
        )
    assert r.status_code == 201
    doc_id = r.json()["id"]

    project_id = client.post("/api/projects", json={"name": "Adopt test"}).json()["id"]
    r = _upload(client, project_id, pdf)
    assert r.status_code == 201
    assert r.json()["id"] == doc_id  # same doc, now owned by the project

    detail = client.get(f"/api/projects/{project_id}").json()
    assert [d["id"] for d in detail["documents"]] == [doc_id]


def test_delete_project_detaches_documents(client):
    project_id = client.post("/api/projects", json={"name": "Detach test"}).json()["id"]
    pdf = TABLES_DIR / "833.1-02-20.pdf"
    doc_id = _upload(client, project_id, pdf).json()["id"]

    r = client.delete(f"/api/projects/{project_id}")
    assert r.status_code == 204
    # the document survives, orphaned
    assert client.get(f"/api/documents/{doc_id}").status_code == 200
