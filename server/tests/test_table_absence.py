"""Nothing is promoted to "materials" without evidence, and an empty answer is
an explicit one.

Maoz's point: a file may simply contain no material table, and that has to be a
correct answer rather than a prompt to go looking harder. The 833.1 sheets are
structural plans — a title block, a revision box, a concrete mix specification
(exposure classes, w/c ratios), a pile schedule, and a dozen stray rulings the
grid detector picks up off the drawing itself. One of those, the pile schedule,
IS material and is covered as a recall case in test_table_recall.py. Everything
else on the sheet must stay out of the BOM.

That makes these sheets the most valuable PRECISION fixture in the repo. The gate
now escalates unreadable Hebrew grids to the VLM instead of dropping them, which
is the right call and also the dangerous direction: a gate that keeps loosening
turns survey northings into a cutting list, and nothing downstream looks wrong —
the numbers are plausible and the order gets placed. So with the VLM off, no grid
on this sheet may come back classified `materials` on deterministic evidence
alone.

The other half is honesty. A document with nothing approvable in it must come
back as an explicit, reviewable empty — rejected grids still listed and still
revivable by hand — not as a silent nothing and not as a crash.
"""

from pathlib import Path

import pytest

TABLES_DIR = Path(__file__).parent.parent.parent / "tables"

pytestmark = pytest.mark.slow

# One sheet is enough: a full job on an A0 Hebrew sheet is ~65s, and the other
# nine fail the same way if they fail at all. The cheap per-grid version of this
# check lives in test_table_recall.py (precision cases) and test_table_gate.py.
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


def test_nothing_is_promoted_to_materials_without_evidence(scanned_plan_sheet):
    """The assertion these sheets exist for. With the VLM off, no grid here can
    be READ — so none of them may be asserted to be a bill of materials. The one
    grid that really is material (the pile schedule) must arrive as `unknown`
    for a human, not as a confident `materials` nobody checked."""
    _project_id, doc, tables = scanned_plan_sheet
    materials = [t for t in tables if t["kind"] == "materials"]
    assert materials == [], (
        f"{doc['filename']}: {len(materials)} grid(s) claimed as materials on a "
        f"sheet whose text the OCR cannot read — "
        f"{[(t['n_rows'], t['n_cols']) for t in materials]}"
    )


def test_the_real_table_survives_as_unknown_not_rejected(scanned_plan_sheet):
    """The recall half, end to end: the 12x7 pile schedule must come back in a
    reviewable state. Before the confidence fix it was status 'rejected' and
    absent from every counter — the sheet reported clean and empty."""
    _project_id, _doc, tables = scanned_plan_sheet
    pile = [t for t in tables if (t["n_rows"], t["n_cols"]) == (12, 7)]
    assert pile, "the pile schedule grid was not detected at all"
    assert pile[0]["kind"] == "unknown", pile[0]["kind"]
    assert pile[0]["status"] != "rejected", "silently dropped again"


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
