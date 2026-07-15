"""End-to-end table extraction job, VLM off: upload -> job -> tables -> rows.

The NCD steel BOM validates itself (qty x unit length = total length, weight
column sums to the printed 3814.4 kg), so even without a VLM the profile rows
must auto-approve on OCR + arithmetic alone.
"""

from pathlib import Path

TABLES_DIR = Path(__file__).parent.parent.parent / "tables"


def test_table_job_ncd_end_to_end(client, wait_job):
    pdf = TABLES_DIR / "NCD5168[_EN](5).pdf"
    project_id = client.post("/api/projects", json={"name": "NCD job"}).json()["id"]
    with open(pdf, "rb") as f:
        doc = client.post(
            f"/api/projects/{project_id}/documents",
            files={"file": (pdf.name, f, "application/pdf")},
        ).json()

    r = client.post(f"/api/documents/{doc['id']}/table-jobs", json={"vlm": False})
    assert r.status_code == 202, r.text
    job = wait_job(client, r.json()["id"])
    assert job["status"] == "done", job["error"]

    tables = client.get(f"/api/documents/{doc['id']}/tables").json()
    assert tables, "no tables persisted"
    bom = max(tables, key=lambda t: t["row_count"])
    assert bom["n_rows"] == 30 and bom["n_cols"] == 7
    # heuristic classification from the header strip BELOW the grid
    assert bom["kind"] == "materials"
    roles = [c["role"] for c in bom["columns"]]
    assert roles[1] == "qty"
    assert bom["declared_total_weight_kg"] == 3814.4
    assert bom["validation"]["weight_total_matches"] is True

    detail = client.get(f"/api/tables/{bom['id']}").json()
    assert len(detail["rows"]) == 30

    by_key = {}
    for row in detail["rows"]:
        by_key.setdefault(row["material_key"], []).append(row)
    # the two 9m legs: L160x160x15, qty 2 each, arithmetic clean -> auto approved
    legs = by_key.get("L160X160X15")
    assert legs and len(legs) == 2
    for leg in legs:
        assert leg["qty"] == 2
        assert leg["unit_length_mm"] == 9000
        assert leg["total_length_mm"] == 18000
        assert leg["status"] == "auto_approved", leg["flags"]

    # nothing wrong slipped through unflagged: every auto-approved row's
    # arithmetic really holds
    for row in detail["rows"]:
        if row["status"] == "auto_approved" and row["qty"] and row["unit_length_mm"]:
            if row["total_length_mm"]:
                assert abs(row["qty"] * row["unit_length_mm"] - row["total_length_mm"]) <= (
                    0.005 * row["total_length_mm"]
                )

    # a table crop was cached for review
    crop = client.get(f"/api/tables/{bom['id']}/crop")
    assert crop.status_code == 200
    assert crop.headers["content-type"] == "image/png"


def test_table_job_rerun_replaces(client, wait_job):
    docs = client.get("/api/documents").json()
    doc = next(d for d in docs if d["filename"].startswith("NCD5168"))
    r = client.post(f"/api/documents/{doc['id']}/table-jobs", json={"vlm": False})
    job = wait_job(client, r.json()["id"])
    assert job["status"] == "done"
    tables = client.get(f"/api/documents/{doc['id']}/tables").json()
    # re-run replaced, not duplicated
    assert len([t for t in tables if t["n_rows"] == 30]) == 1


def test_delete_document_with_tables(client, wait_job):
    """A document that has been table-scanned must still delete cleanly — the
    material_tables/rows and their crops reference jobs and pages, so a naive
    delete hits a FOREIGN KEY constraint (regression: the delete 500'd)."""
    pdf = TABLES_DIR / "833.1-01-20.pdf"
    with open(pdf, "rb") as f:
        doc = client.post(
            "/api/documents", files={"file": (pdf.name, f, "application/pdf")}
        ).json()
    r = client.post(f"/api/documents/{doc['id']}/table-jobs", json={"vlm": False})
    assert wait_job(client, r.json()["id"])["status"] == "done"
    assert client.get(f"/api/documents/{doc['id']}/tables").json()  # tables exist

    r = client.delete(f"/api/documents/{doc['id']}")
    assert r.status_code == 204, r.text
    assert client.get(f"/api/documents/{doc['id']}").status_code == 404
