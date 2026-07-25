""""This document has no material table" is a correct answer, not a failure.

Maoz's point, and the sample set proves it: the ten 833.1 sheets are structural
plans. Their only real tables are a title block, a revision box, a concrete mix
specification and a pile setting-out schedule. Reading every cell of the two
largest confirmed it — the mix table holds exposure classes and w/c ratios, the
pile table holds NORTHING/EASTING survey coordinates. There is no BOM on those
sheets to find.

That makes them the most valuable PRECISION fixture in the repo. A gate that
gets greedy — chasing a missing table by loosening the keyword rule, or letting
the VLM classify every grid it cannot read — turns survey coordinates into a
cutting list. That failure costs more than a missed table, because nothing
downstream looks wrong: the numbers are plausible, the order is placed.

The other half is the honesty requirement. A document with no material table
must come back as an explicit, reviewable empty — rejected grids still listed
and still revivable by hand — not as a silent nothing.
"""

from pathlib import Path

import pytest

TABLES_DIR = Path(__file__).parent.parent.parent / "tables"

pytestmark = pytest.mark.slow

# Structural plan sheets confirmed to carry no bill of materials. One is enough:
# a full job on an A0 Hebrew sheet is ~65s, and the other nine fail the same way
# if they fail at all. The cheap per-grid version of this check lives in
# test_table_recall.py (precision cases) and test_table_gate.py.
NO_BOM_SHEETS = ["833.1-01-20.pdf"]


@pytest.fixture(scope="module", params=NO_BOM_SHEETS)
def scanned_plan_sheet(client, wait_job, request):
    pdf = TABLES_DIR / request.param
    project_id = client.post(
        "/api/projects", json={"name": f"NoBOM {request.param}"}
    ).json()["id"]
    with open(pdf, "rb") as f:
        doc = client.post(
            f"/api/projects/{project_id}/documents",
            files={"file": (pdf.name, f, "application/pdf")},
        ).json()
    job = wait_job(
        client,
        client.post(
            f"/api/documents/{doc['id']}/table-jobs", json={"vlm": False}
        ).json()["id"],
    )
    assert job["status"] == "done", job["error"]
    tables = client.get(f"/api/documents/{doc['id']}/tables").json()
    return project_id, doc, tables


def test_no_material_table_is_invented(scanned_plan_sheet):
    """The assertion these sheets exist for."""
    _project_id, doc, tables = scanned_plan_sheet
    materials = [t for t in tables if t["kind"] == "materials"]
    assert materials == [], (
        f"{doc['filename']}: invented {len(materials)} material table(s) "
        f"from a drawing that has none — "
        f"{[(t['n_rows'], t['n_cols']) for t in materials]}"
    )


def test_no_rows_reach_the_summary(client, scanned_plan_sheet):
    project_id, _doc, _tables = scanned_plan_sheet
    summary = client.get(f"/api/projects/{project_id}/summary").json()
    assert summary["rows"] == []
    assert summary["totals"]["total_weight_kg"] == 0


def test_the_empty_answer_is_explicit_not_a_crash(client, scanned_plan_sheet):
    """A sheet with no BOM must still scan cleanly and report a settled state:
    nothing pending, nothing flagged, no error. 'Nothing here' and 'the job
    fell over' must not look the same to the operator."""
    project_id, _doc, tables = scanned_plan_sheet
    summary = client.get(f"/api/projects/{project_id}/summary").json()
    assert summary["unreviewed"]["pending_tables"] == 0
    assert summary["unreviewed"]["needs_review_rows"] == 0
    assert tables, "the scan found no grids at all — that is a detection failure"


def test_rejected_grids_stay_visible_and_revivable(client, scanned_plan_sheet):
    """The escape hatch. If a sheet DOES hold a table we failed to recognise,
    the operator must be able to see the grid and promote it by hand. A rejected
    table that cannot be revived turns a recall miss into a dead end."""
    _project_id, _doc, tables = scanned_plan_sheet
    rejected = [t for t in tables if t["status"] == "rejected"]
    assert rejected, "nothing rejected — expected the title block at least"

    target = max(rejected, key=lambda t: t["n_rows"] * t["n_cols"])
    r = client.patch(
        f"/api/tables/{target['id']}",
        json={"action": "set_kind", "kind": "materials"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == "materials"
    assert r.json()["status"] == "pending", "revived table must re-enter review"
