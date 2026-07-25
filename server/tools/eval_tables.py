"""Cell-extraction accuracy against the ground-truth fixtures.

Usage (from server/):  uv run python tools/eval_tables.py

Prints per-column-role accuracy per fixture and the metric that actually
matters: the UNFLAGGED-WRONG rate. A wrong cell that is flagged costs the
operator a click; a wrong cell that auto-approves costs money. Target: 0.
"""

import json
import sys
from pathlib import Path

import fitz

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.tables.cells import read_matrix  # noqa: E402
from app.tables.grid import detect_grids  # noqa: E402
from app.tables.normalize import fix_homoglyphs, parse_number  # noqa: E402
from app.tables.validate import validate_row  # noqa: E402

TABLES_DIR = Path(__file__).parent.parent.parent / "tables"
FIXTURES_DIR = Path(__file__).parent.parent / "tests" / "fixtures" / "tables"

NUMERIC_ROLES = {"item_no", "qty", "diameter", "unit_length", "total_length",
                 "unit_weight", "total_weight", "level"}


def norm(text: str) -> str:
    return fix_homoglyphs(text or "").replace(" ", "").replace(",", "").lower()


def bbox_iou(a, b) -> float:
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inter = (ix1 - ix0) * (iy1 - iy0)
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    return inter / (area_a + area_b - inter)


def main() -> None:
    grand = {"cells": 0, "correct": 0, "wrong_flagged": 0, "wrong_unflagged": 0}
    for gt_path in sorted(FIXTURES_DIR.glob("*.json")):
        gt = json.loads(gt_path.read_text(encoding="utf-8"))
        pdf = TABLES_DIR / f"{gt_path.stem}.pdf"
        page = fitz.open(pdf)[0]
        grids = detect_grids(page)
        for expected in gt["tables"]:
            if "cells" not in expected:
                continue
            # match on WHERE the table is, not on its shape: two grids on a sheet
            # can share row/col counts, and picking the wrong one reports a false
            # 0% that looks like an OCR collapse
            grid = max(grids, key=lambda g: bbox_iou(g.bbox, expected["bbox"]))
            iou = bbox_iou(grid.bbox, expected["bbox"])
            if iou < 0.8:
                print(f"  SKIP {gt_path.stem}/{expected['name']}: no grid matches "
                      f"the ground-truth bbox (best IoU {iou:.2f})")
                continue
            roles = expected["column_roles"]
            matrix = read_matrix(page, grid, list(range(grid.n_rows)), dpi=864)
            per_role: dict[str, list[int]] = {}
            for r, row_gt in enumerate(expected["cells"]):
                fields = {
                    "qty": None, "unit_length_mm": None, "total_length_mm": None,
                    "unit_weight_kg": None, "total_weight_kg": None,
                }
                for c, role in enumerate(roles):
                    if role in ("qty",):
                        fields["qty"] = parse_number(matrix[r][c].value)
                    elif role == "unit_length":
                        fields["unit_length_mm"] = parse_number(matrix[r][c].value)
                    elif role == "total_length":
                        fields["total_length_mm"] = parse_number(matrix[r][c].value)
                    elif role == "unit_weight":
                        fields["unit_weight_kg"] = parse_number(matrix[r][c].value)
                    elif role == "total_weight":
                        fields["total_weight_kg"] = parse_number(matrix[r][c].value)
                flagged = bool(validate_row(fields, roles).flags)
                for c, role in enumerate(roles):
                    got, want = norm(matrix[r][c].value or ""), norm(row_gt[c])
                    bucket = per_role.setdefault(role, [0, 0])
                    bucket[0] += 1
                    grand["cells"] += 1
                    if got == want:
                        bucket[1] += 1
                        grand["correct"] += 1
                    elif flagged or matrix[r][c].ocr_conf < 0.85:
                        grand["wrong_flagged"] += 1
                    else:
                        grand["wrong_unflagged"] += 1
                        print(f"  UNFLAGGED WRONG {gt_path.stem} r{r} {role}: "
                              f"{got!r} != {want!r}")
            print(f"{gt_path.stem} / {expected['name']}:")
            for role, (total, correct) in sorted(per_role.items()):
                marker = " <- numeric" if role in NUMERIC_ROLES else ""
                print(f"  {role:14s} {correct:3d}/{total:<3d} "
                      f"({correct / total:6.1%}){marker}")
    print("\n=== grand total ===")
    print(f"cells: {grand['cells']}, correct: {grand['correct']} "
          f"({grand['correct'] / max(grand['cells'], 1):.1%})")
    print(f"wrong but flagged:  {grand['wrong_flagged']} (safe — operator sees them)")
    print(f"wrong and UNFLAGGED: {grand['wrong_unflagged']} (the dangerous quadrant)")


if __name__ == "__main__":
    main()
