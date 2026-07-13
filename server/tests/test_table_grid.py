"""Deterministic grid-recovery tests — no OCR, no VLM, no server."""

import json
from pathlib import Path

import fitz
import pytest

from app.tables.grid import TableGrid, detect_grids
from app.tables.regions import render_region

TABLES_DIR = Path(__file__).parent.parent.parent / "tables"
FIXTURES_DIR = Path(__file__).parent / "fixtures" / "tables"

ALL_PDFS = sorted(TABLES_DIR.glob("*.pdf"))
GROUND_TRUTH = sorted(FIXTURES_DIR.glob("*.json"))


def _bbox_iou(a, b) -> float:
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inter = (ix1 - ix0) * (iy1 - iy0)
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    return inter / (area_a + area_b - inter)


@pytest.mark.parametrize("gt_path", GROUND_TRUTH, ids=lambda p: p.stem)
def test_ground_truth_tables_found(gt_path):
    gt = json.loads(gt_path.read_text(encoding="utf-8"))
    pdf = TABLES_DIR / f"{gt_path.stem}.pdf"
    page = fitz.open(pdf)[0]
    assert page.rotation == gt["page_rotation"]

    grids = detect_grids(page)
    for expected in gt["tables"]:
        match = max(grids, key=lambda g: _bbox_iou(g.bbox, expected["bbox"]))
        iou = _bbox_iou(match.bbox, expected["bbox"])
        assert iou >= 0.8, f"{expected['name']}: best IoU {iou:.2f}"
        assert match.n_rows == expected["rows"], expected["name"]
        assert match.n_cols == expected["cols"], expected["name"]


@pytest.mark.parametrize("pdf", ALL_PDFS, ids=lambda p: p.name)
def test_every_sample_yields_sane_grids(pdf):
    page = fitz.open(pdf)[0]
    grids = detect_grids(page)
    assert grids, "no grids found at all"
    for g in grids:
        assert g.n_rows >= 2 and g.n_cols >= 2
        assert g.col_edges == sorted(g.col_edges)
        assert g.row_edges == sorted(g.row_edges)
        x0, y0, x1, y1 = g.bbox
        assert x0 < x1 and y0 < y1
        # bbox within the display page rect
        assert x1 <= page.rect.width + 1 and y1 <= page.rect.height + 1


def test_detection_is_deterministic():
    page = fitz.open(ALL_PDFS[0])[0]
    a = detect_grids(page)
    b = detect_grids(page)
    assert [(g.bbox, g.col_edges, g.row_edges) for g in a] == [
        (g.bbox, g.col_edges, g.row_edges) for g in b
    ]


def test_cell_rect_indexing():
    grid = TableGrid(bbox=(0, 0, 30, 20), col_edges=[0, 10, 30], row_edges=[0, 5, 20])
    assert grid.n_rows == 2 and grid.n_cols == 2
    assert grid.cell_rect(0, 0) == (0, 0, 10, 5)
    assert grid.cell_rect(1, 1) == (10, 5, 30, 20)


def test_render_region_rotated_page_is_upright():
    """Regression guard for the rotation landmine: the clip is display-space."""
    gt = json.loads(
        (FIXTURES_DIR / "833.1-01-20.json").read_text(encoding="utf-8")
    )
    page = fitz.open(TABLES_DIR / "833.1-01-20.pdf")[0]
    bbox = gt["tables"][0]["bbox"]  # 556 x 270 pt — wider than tall
    png = render_region(page, tuple(bbox), dpi=150)
    pix = fitz.Pixmap(png)
    assert pix.width > pix.height, "crop came out sideways or empty"
    # non-trivial content: not a blank strip
    assert pix.width > 300
