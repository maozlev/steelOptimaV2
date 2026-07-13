"""Review flow: flagged rows -> human decisions -> table approval -> summary."""

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
    raise TimeoutError(f"job {job_id} did not finish within {timeout}s")


@pytest.fixture(scope="module")
def ncd_table(client):
    pdf = TABLES_DIR / "NCD5168[_EN](5).pdf"
    project_id = client.post("/api/projects", json={"name": "Review flow"}).json()["id"]
    with open(pdf, "rb") as f:
        doc = client.post(
            f"/api/projects/{project_id}/documents",
            files={"file": (pdf.name, f, "application/pdf")},
        ).json()
    r = client.post(f"/api/documents/{doc['id']}/table-jobs", json={"vlm": False})
    job = _wait(client, r.json()["id"])
    assert job["status"] == "done", job["error"]
    tables = client.get(f"/api/documents/{doc['id']}/tables").json()
    bom = max(tables, key=lambda t: t["row_count"])
    return project_id, doc, bom


def test_approve_blocked_while_flagged(client, ncd_table):
    project_id, doc, bom = ncd_table
    detail = client.get(f"/api/tables/{bom['id']}").json()
    flagged = [r for r in detail["rows"] if r["status"] == "needs_review"]
    if flagged:
        r = client.patch(f"/api/tables/{bom['id']}", json={"action": "approve"})
        assert r.status_code == 409

    # summary is empty before approval, and says so
    summary = client.get(f"/api/projects/{project_id}/summary").json()
    assert summary["rows"] == []
    assert summary["unreviewed"]["pending_tables"] >= 1


def test_review_and_approve_flow(client, ncd_table):
    project_id, doc, bom = ncd_table
    pending_before = client.get(f"/api/projects/{project_id}/summary").json()[
        "unreviewed"
    ]["pending_tables"]
    detail = client.get(f"/api/tables/{bom['id']}").json()

    for row in detail["rows"]:
        if row["status"] != "needs_review":
            continue
        # a human approves the flagged plate/reinforcement rows
        r = client.patch(
            f"/api/material-rows/{row['id']}", json={"action": "approve"}
        )
        assert r.status_code == 200, r.text

    r = client.patch(f"/api/tables/{bom['id']}", json={"action": "approve"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "approved"

    summary = client.get(f"/api/projects/{project_id}/summary").json()
    # the approved BOM left the pending pool; the sheet's junk grids (title
    # block fragments, "unknown" offline) honestly remain in it
    assert summary["unreviewed"]["pending_tables"] == pending_before - 1
    keys = {row["material_key"] for row in summary["rows"]}
    assert "L160X160X15" in keys
    legs = next(r for r in summary["rows"] if r["material_key"] == "L160X160X15")
    assert legs["qty"] == 4  # 2 items x qty 2
    assert legs["total_length_mm"] == 36000
    assert legs["lengths"] == [{"unit_length_mm": 9000, "qty": 4}]
    # weight column reconciled against the printed total earlier; the summary
    # total must be in the same ballpark (some rows may have been rejected)
    assert summary["totals"]["total_weight_kg"] > 3000

    # cross-project rollup returns the same content for a single id
    rollup = client.get(f"/api/projects-summary?ids={project_id}").json()
    assert rollup["totals"] == summary["totals"]


def test_row_edit_reruns_validation(client, ncd_table):
    project_id, doc, bom = ncd_table
    # approved table locks rows
    detail = client.get(f"/api/tables/{bom['id']}").json()
    row = detail["rows"][5]
    r = client.patch(
        f"/api/material-rows/{row['id']}",
        json={"action": "edit", "fields": {"qty": 9}},
    )
    assert r.status_code == 409

    client.patch(f"/api/tables/{bom['id']}", json={"action": "reopen"})
    r = client.patch(
        f"/api/material-rows/{row['id']}",
        json={"action": "edit", "fields": {"qty": 9}},
    )
    assert r.status_code == 200, r.text
    edited = r.json()
    assert edited["status"] == "edited"
    assert edited["qty"] == 9
    # 9 x unit_length no longer equals total_length -> the edit gets flagged
    assert "qty_x_unit_length_mismatch" in edited["flags"]

    # put it back and re-approve the table
    r = client.patch(
        f"/api/material-rows/{row['id']}",
        json={"action": "edit", "fields": {"qty": row["qty"]}},
    )
    assert r.json()["flags"] == []
    client.patch(f"/api/tables/{bom['id']}", json={"action": "approve"})


def test_set_kind_revives_rejected_table(client, ncd_table):
    project_id, doc, bom = ncd_table
    tables = client.get(f"/api/documents/{doc['id']}/tables").json()
    other = next((t for t in tables if t["id"] != bom["id"]), None)
    if other is None:
        pytest.skip("only one table detected")
    r = client.patch(
        f"/api/tables/{other['id']}", json={"action": "set_kind", "kind": "materials"}
    )
    assert r.status_code == 200
    assert r.json()["kind"] == "materials"
    assert r.json()["status"] == "pending"
