"""L0 — table recall on the real sheets: does a known material table SURVIVE?

test_table_grid.py already proves the geometry is found. This file tests the
next question, which nothing covered: having found the grid, does the pipeline
keep it? A found-then-dropped BOM is worth exactly as much as a missed one, and
costs more to debug because the grid IS in the logs.

Runs the shipped path (cells.header_candidates -> classify.gate_decision) with
the VLM off, over only the grids that ground truth points at, so it stays a few
seconds rather than the several minutes a full-sheet sweep takes.
"""

import json
from pathlib import Path

import fitz
import pytest

from app.tables.cells import header_candidates
from app.tables.classify import gate_decision
from app.tables.grid import detect_grids

TABLES_DIR = Path(__file__).parent.parent.parent / "tables"
FIXTURES_DIR = Path(__file__).parent / "fixtures" / "tables"
OCR_DPI = 864

# (fixture stem, table name) for every ground-truth table that declares a kind
CASES = []
for _gt in sorted(FIXTURES_DIR.glob("*.json")):
    for _t in json.loads(_gt.read_text(encoding="utf-8"))["tables"]:
        if "expected_kind" in _t:
            CASES.append((_gt.stem, _t["name"]))

# Sheets whose material tables the gate currently drops. See
# test_table_gate.py::test_hebrew_material_table_is_not_silently_dropped for the
# mechanism — the Hebrew header OCRs to Latin garbage, which counts as "readable".
KNOWN_DROPPED = {"833.1-01-20"}


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
    assert _iou(grid.bbox, expected["bbox"]) >= 0.8, "geometry regressed — see test_table_grid"
    return expected, gate_decision(header_candidates(page, grid, OCR_DPI), grid.n_cols)


@pytest.mark.parametrize("stem,name", CASES, ids=lambda v: str(v))
def test_material_table_is_not_silently_dropped(stem, name, request):
    """THE assertion. A ground-truth material table must not end as kind 'other'
    (-> status 'rejected'), because a rejected table is excluded from the summary
    AND from the summary's own 'unreviewed' counters: the sheet vanishes without
    a trace anyone can see."""
    expected, decision = _decide(stem, name)
    if expected["expected_kind"] != "materials":
        pytest.skip("precision case, covered by the junk tests in test_table_gate")
    if stem in KNOWN_DROPPED:
        request.node.add_marker(
            pytest.mark.xfail(
                strict=True,
                reason=f"KNOWN BUG: {stem} is Hebrew — the gate drops its BOMs "
                "silently. Remove this sheet from KNOWN_DROPPED once fixed.",
            )
        )
    assert not decision.silently_dropped, (
        f"{stem}/{name}: gate said {decision.reason!r} "
        f"(readable={decision.readable}, markers={decision.markers}) — "
        "this sheet contributes nothing and nothing reports it"
    )


@pytest.mark.parametrize("stem,name", CASES, ids=lambda v: str(v))
def test_material_table_reaches_the_row_reader(stem, name, request):
    """One step stronger than 'not dropped': the table must end up classified
    materials (rows read + validated) or handed to the VLM — not parked in a
    state where rows are never read at all."""
    expected, decision = _decide(stem, name)
    if expected["expected_kind"] != "materials":
        pytest.skip("precision case")
    if stem in KNOWN_DROPPED:
        request.node.add_marker(pytest.mark.xfail(strict=True, reason="see above"))
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
