"""OCR accuracy floor on the NCD steel BOM — no VLM, pure OCR + normalization.

The floors are a regression ratchet: they are set just under the measured
accuracy (numeric 100%, text 100% after homoglyph fixing at zoom 12). If a
change drops below them, the change broke reading, not the fixture.
"""

import json
from pathlib import Path

import fitz
import pytest

from app.tables.cells import read_matrix
from app.tables.grid import detect_grids
from app.tables.normalize import fix_homoglyphs

TABLES_DIR = Path(__file__).parent.parent.parent / "tables"
FIXTURE = (
    Path(__file__).parent / "fixtures" / "tables" / "NCD5168[_EN](5).json"
)

NUMERIC_FLOOR = 0.97
TEXT_FLOOR = 0.90


@pytest.fixture(scope="module")
def ncd():
    gt = json.loads(FIXTURE.read_text(encoding="utf-8"))["tables"][0]
    page = fitz.open(TABLES_DIR / "NCD5168[_EN](5).pdf")[0]
    grids = detect_grids(page)
    grid = max(grids, key=lambda g: g.n_rows * g.n_cols)
    assert grid.n_rows == gt["rows"] and grid.n_cols == gt["cols"]
    matrix = read_matrix(page, grid, list(range(grid.n_rows)), dpi=864)
    return gt, matrix


def _norm(text: str) -> str:
    return fix_homoglyphs(text or "").replace(" ", "").replace(",", "").lower()


def test_numeric_cell_accuracy(ncd):
    gt, matrix = ncd
    numeric_cols = [
        i
        for i, role in enumerate(gt["column_roles"])
        if role in ("item_no", "qty", "unit_length", "total_length", "total_weight")
    ]
    total = correct = 0
    misses = []
    for r, expected_row in enumerate(gt["cells"]):
        for c in numeric_cols:
            total += 1
            got = _norm(matrix[r][c].value or "")
            want = _norm(expected_row[c])
            if got == want:
                correct += 1
            else:
                misses.append(f"r{r}c{c}: {got!r} != {want!r}")
    accuracy = correct / total
    assert accuracy >= NUMERIC_FLOOR, f"{accuracy:.1%}; misses: {misses[:10]}"


def test_description_cell_accuracy(ncd):
    gt, matrix = ncd
    col = gt["column_roles"].index("description")
    total = correct = 0
    misses = []
    for r, expected_row in enumerate(gt["cells"]):
        total += 1
        got = _norm(matrix[r][col].value or "")
        want = _norm(expected_row[col])
        if got == want:
            correct += 1
        else:
            misses.append(f"r{r}: {got!r} != {want!r}")
    accuracy = correct / total
    assert accuracy >= TEXT_FLOOR, f"{accuracy:.1%}; misses: {misses[:10]}"


def test_empty_cells_read_as_empty(ncd):
    gt, matrix = ncd
    col = gt["column_roles"].index("other")  # notes: 2 filled, 28 empty
    empties = sum(
        1
        for r, expected_row in enumerate(gt["cells"])
        if expected_row[col] == "" and matrix[r][col].source == "empty"
    )
    assert empties >= 26
