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
            grid = max(
                grids,
                key=lambda g: -abs(g.n_rows - expected["rows"])
                - abs(g.n_cols - expected["cols"]),
            )
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
