"""L4 — do the numbers that leave the pipeline still add up?

Two independent checks, because they fail in different ways:

1. Against the sheet's OWN printed grand total. The strongest signal the pipeline
   has and the cheapest to verify: real sheets print it, so this needs no
   labelling at all. Covers the real NCD BOM.
2. Across a multi-document project. Aggregation regroups rows by material key and
   sums them; a bug there (double counting a re-run's tables, dropping a
   document, mis-grouping) shows up nowhere else — every individual table still
   validates perfectly.

Plus the honesty check: a summary that silently omits a table must at least say
something is outstanding. Today a table the gate rejected is counted in NEITHER
the rows nor the "unreviewed" block (aggregate.py only counts kind=="materials"),
so a dropped sheet reads as a clean, complete project.
"""

from pathlib import Path

import pytest

from tests.bom_factory import (
    approve_everything,
    bom_row,
    declared_total_of,
    upload_bom,
)

pytestmark = pytest.mark.slow  # OCR / CV / full job — see pyproject markers

TABLES_DIR = Path(__file__).parent.parent.parent / "tables"
NCD_PRINTED_TOTAL_KG = 3814.4  # printed on the sheet; see the NCD ground truth


# --- 1. against the sheet's own printed total ---------------------------------

@pytest.fixture(scope="module")
def approved_ncd(client):
    import time

    pdf = TABLES_DIR / "NCD5168[_EN](5).pdf"
    project_id = client.post("/api/projects", json={"name": "Reconcile"}).json()["id"]
    with open(pdf, "rb") as f:
        doc = client.post(f"/api/projects/{project_id}/documents",
                          files={"file": (pdf.name, f, "application/pdf")}).json()
    job_id = client.post(f"/api/documents/{doc['id']}/table-jobs",
                         json={"vlm": False}).json()["id"]
    deadline = time.time() + 300
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("done", "failed"):
            break
        time.sleep(0.2)
    assert job["status"] == "done", job["error"]
    tables = client.get(f"/api/documents/{doc['id']}/tables").json()
    bom = max(tables, key=lambda t: t["row_count"])
    approve_everything(client, bom["id"])
    return project_id, bom


def test_ncd_summary_equals_the_printed_grand_total(client, approved_ncd):
    """Every approved row's weight, regrouped and re-summed by the aggregator,
    must still come back to the number printed on the drawing. This is the one
    end-to-end accuracy claim the product can make without any labelling."""
    project_id, _ = approved_ncd
    summary = client.get(f"/api/projects/{project_id}/summary").json()
    got = summary["totals"]["total_weight_kg"]
    # same tolerance the table-level checksum uses: relative, plus one unit of
    # per-row rounding (the sheet prints weights to 0.1 kg over 30 rows)
    tol = max(0.005 * NCD_PRINTED_TOTAL_KG, 0.05 * 30)
    assert abs(got - NCD_PRINTED_TOTAL_KG) <= tol, (
        f"summary {got} kg vs printed {NCD_PRINTED_TOTAL_KG} kg"
    )


def test_ncd_summary_is_not_silently_partial(client, approved_ncd):
    project_id, _ = approved_ncd
    summary = client.get(f"/api/projects/{project_id}/summary").json()
    assert summary["unreviewed"]["pending_tables"] == 0
    assert summary["unreviewed"]["needs_review_rows"] == 0
    assert summary["rows"], "approved a whole BOM and the summary is empty"


# --- 2. across a multi-document project ---------------------------------------

SHEETS = {
    "sheet_a": [bom_row(801, 4, 1052), bom_row(802, 8, 743)],
    "sheet_b": [bom_row(803, 2, 2077), bom_row(804, 6, 1357)],
    "sheet_c": [bom_row(805, 3, 900), bom_row(806, 5, 1200)],
}


@pytest.fixture(scope="module")
def three_sheet_project(client, wait_job, tmp_path_factory):
    tmp = tmp_path_factory.mktemp("recon")
    project_id = client.post("/api/projects", json={"name": "Three"}).json()["id"]
    printed = []
    for name, rows in SHEETS.items():
        out = upload_bom(client, wait_job, tmp, project_id, name, rows)
        approve_everything(client, out["table"]["id"])
        printed.append(out["printed"])
    return project_id, printed


def test_project_total_is_the_sum_of_its_sheets(client, three_sheet_project):
    project_id, printed = three_sheet_project
    summary = client.get(f"/api/projects/{project_id}/summary").json()
    assert summary["totals"]["total_weight_kg"] == pytest.approx(sum(printed), abs=0.15)


def test_same_profile_across_sheets_pools_into_one_line(client, three_sheet_project):
    """All six rows are L60x60x6. The whole point of the summary is that they
    become ONE order line — six lines means the material key is unstable and the
    optimizer will buy six times."""
    project_id, _ = three_sheet_project
    summary = client.get(f"/api/projects/{project_id}/summary").json()
    lines = [r for r in summary["rows"] if r["material_key"] == "L60X60X6"]
    assert len(lines) == 1, [r["material_key"] for r in summary["rows"]]
    line = lines[0]

    expected_qty = sum(int(r["qty"]) for rows in SHEETS.values() for r in rows)
    expected_len = sum(int(r["total"]) for rows in SHEETS.values() for r in rows)
    assert line["qty"] == expected_qty
    assert line["total_length_mm"] == pytest.approx(expected_len)
    assert len(line["documents"]) == 3, line["documents"]
    # the cut lengths must survive individually — the optimizer needs the pieces,
    # not the total. Summing lengths and losing the breakdown is a silent killer.
    assert {entry["unit_length_mm"] for entry in line["lengths"]} == {
        float(r["unit"]) for rows in SHEETS.values() for r in rows
    }


def test_rerunning_a_sheet_does_not_double_count(client, wait_job, tmp_path):
    """A re-run replaces its own tables. If it ever appends instead, every number
    downstream doubles and every individual table still validates perfectly."""
    project_id = client.post("/api/projects", json={"name": "Rerun"}).json()["id"]
    rows = [bom_row(810, 3, 1000), bom_row(811, 2, 1500)]
    out = upload_bom(client, wait_job, tmp_path, project_id, "rerun", rows)

    job = wait_job(client, client.post(
        f"/api/documents/{out['doc']['id']}/table-jobs", json={"vlm": False}
    ).json()["id"])
    assert job["status"] == "done"

    tables = client.get(f"/api/documents/{out['doc']['id']}/tables").json()
    boms = [t for t in tables if t["kind"] == "materials"]
    assert len(boms) == 1, f"{len(boms)} material tables after re-run"
    approve_everything(client, boms[0]["id"])

    summary = client.get(f"/api/projects/{project_id}/summary").json()
    assert summary["totals"]["total_weight_kg"] == pytest.approx(
        declared_total_of(rows), abs=0.15
    )
