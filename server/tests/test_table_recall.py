"""L0 — on the real sheets, does the right table survive and the wrong one not?

test_table_grid.py proves the geometry is found. This file tests what happens
next, which nothing covered: having found a grid, does the pipeline keep it?

Both directions matter and they fail differently:

- RECALL. A found-then-dropped BOM is worth as much as one never found, and
  costs more to debug because the grid IS in the logs. Worse, a dropped table is
  invisible: aggregate.py counts only kind=="materials" toward pending_tables /
  needs_review_rows, so the sheet contributes nothing AND reports nothing.
- PRECISION. The 833.1 sheets are structural plans that carry NO bill of
  materials — only a concrete mix spec, a pile setting-out schedule, a title
  block and a revision box. A gate that got greedy would turn survey coordinates
  into an order. "This document has no material table" is a correct answer and
  these sheets are what pins it.

Runs the shipped path (cells.header_candidates -> classify.gate_decision) with
the VLM off, over only the grids ground truth points at, so it stays seconds
rather than the minutes a full-sheet sweep takes.
"""

import json
from pathlib import Path

import fitz
import pytest

from app.tables.cells import header_candidates
from app.tables.classify import gate_decision
from app.tables.grid import detect_grids

pytestmark = pytest.mark.slow  # OCR / CV / full job — see pyproject markers

TABLES_DIR = Path(__file__).parent.parent.parent / "tables"
FIXTURES_DIR = Path(__file__).parent / "fixtures" / "tables"
OCR_DPI = 864

CASES = []
for _gt in sorted(FIXTURES_DIR.glob("*.json")):
    for _t in json.loads(_gt.read_text(encoding="utf-8"))["tables"]:
        if "expected_kind" in _t:
            CASES.append((_gt.stem, _t["name"], _t["expected_kind"]))

MATERIALS = [c for c in CASES if c[2] == "materials"]
NOT_MATERIALS = [c for c in CASES if c[2] != "materials"]


def _iou(a, b) -> float:
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inter = (ix1 - ix0) * (iy1 - iy0)
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    return inter / (area_a + area_b - inter)


def _decide(stem: str, name: str):
    gt = json.loads((FIXTURES_DIR / f"{stem}.json").read_text(encoding="utf-8"))
    expected = next(t for t in gt["tables"] if t["name"] == name)
    page = fitz.open(TABLES_DIR / f"{stem}.pdf")[0]
    grids = detect_grids(page)
    grid = max(grids, key=lambda g: _iou(g.bbox, expected["bbox"]))
    assert _iou(grid.bbox, expected["bbox"]) >= 0.8, (
        "geometry regressed — see test_table_grid"
    )
    return expected, gate_decision(header_candidates(page, grid, OCR_DPI), grid.n_cols)


# --- recall -------------------------------------------------------------------

@pytest.mark.parametrize("stem,name,kind", MATERIALS, ids=lambda v: str(v))
def test_material_table_is_not_silently_dropped(stem, name, kind):
    """A ground-truth material table must not end as kind 'other' (-> status
    'rejected'), because a rejected table is excluded from the summary AND from
    the summary's own 'unreviewed' counters: the sheet vanishes without a trace."""
    _expected, decision = _decide(stem, name)
    assert not decision.silently_dropped, (
        f"{stem}/{name}: gate said {decision.reason!r} "
        f"(readable={decision.readable}, markers={decision.markers})"
    )


@pytest.mark.parametrize("stem,name,kind", MATERIALS, ids=lambda v: str(v))
def test_material_table_reaches_the_row_reader(stem, name, kind):
    """One step stronger than 'not dropped': the table must be classified
    materials (rows read + validated) or handed to the VLM — not parked in a
    state where rows are never read at all."""
    _expected, decision = _decide(stem, name)
    cls = decision.classification
    assert cls is None or cls.kind in ("materials", "unknown"), (
        f"{stem}/{name}: classified {cls.kind!r} — rows will never be read"
    )


def test_ncd_column_roles_match_ground_truth():
    """L1 on the one sheet whose roles are labelled. The heuristic must reproduce
    them with the VLM off — a mislabelled unit_length/total_length pair makes
    every row's arithmetic check pass against the wrong number."""
    expected, decision = _decide("NCD5168[_EN](5)", "bom")
    assert decision.classification is not None
    assert decision.classification.column_roles == expected["column_roles"]


# --- precision ----------------------------------------------------------------

@pytest.mark.parametrize("stem,name,kind", NOT_MATERIALS, ids=lambda v: str(v))
def test_non_material_table_never_classifies_as_materials(stem, name, kind):
    """A concrete mix spec and a pile coordinate schedule both carry numbers,
    quantities and dimensions. Neither is an order. Turning survey northings
    into a cutting list is the failure mode that costs more than a missed table,
    because nothing downstream would look wrong."""
    _expected, decision = _decide(stem, name)
    cls = decision.classification
    assert cls is None or cls.kind != "materials", (
        f"{stem}/{name} classified as materials via {decision.reason!r}"
    )
