"""Invent synthetic steel-BOM PDFs that the tables pipeline reads like the real ones.

NOT eval fixtures. Synthetic tables carry clean, solid text that the OCR reads
easily — they will NOT reproduce the failure modes the pipeline's hard-won rules
exist for (hairline stroke glyphs, AA-gray digits, letter-spaced fragmentation,
Hebrew). Growing tests/fixtures/ground_truth needs REAL drawings. What this IS
good for: more material tables to feed the pricing / optimizer / aggregation
pages, and to load-test the scan queue.

Each file is built so the deterministic (VLM-off) path auto-approves every row:
  * a ruled grid whose header row says "Qty"/"Unit Length"/... -> the heuristic
    classifies it "materials" with no VLM needed (app/tables/classify.py),
  * qty x unit_length == total_length exactly -> the row's own checksum holds
    (app/tables/validate.py),
  * a printed "Total Weight: N kg" that equals the weight column's sum -> the
    table-level checksum reconciles and boosts every row to 0.95 confidence
    (app/tables/service.py), clearing the 0.80 approve threshold.

Run:
    uv run python tools/make_synthetic_tables.py --count 5 --rows 18
    uv run python tools/make_synthetic_tables.py --out ../synthetic_tables --seed 7
Then upload the PDFs into a "material tables" project from the UI.
"""

from __future__ import annotations

import argparse
import random
from pathlib import Path

import fitz

# (profile label, kg per metre) — plausible equal-leg angles; the exact physics
# does not matter, only that the weight column is positive and sums to the total.
PROFILES: list[tuple[str, float]] = [
    ("L50x50x5", 3.77),
    ("L60x60x6", 5.42),
    ("L70x70x7", 7.38),
    ("L80x80x8", 9.63),
    ("L90x90x9", 12.20),
    ("L100x100x10", 15.00),
    ("L120x120x11", 19.90),
    ("L160x160x15", 35.80),
]
DESCRIPTIONS = ["Horizontal", "Diagonal", "Leg", "Bracing", "Strut", "Cross Brace"]

# column header text -> width in points. Headers are chosen to hit the keyword
# map in app/tables/classify.py so the table classifies without a VLM.
COLUMNS: list[tuple[str, float]] = [
    ("Item No", 46),
    ("Qty", 34),
    ("Item Description", 118),
    ("Profile", 96),
    ("Unit Length [mm]", 80),
    ("Total Length [mm]", 84),
    ("Total Weight [kg]", 84),
    ("Notes", 120),
]

MARGIN = 44.0
ROW_H = 22.0
HEADER_H = 26.0
CELL_INSET = 4.0
RULE_W = 0.8
TITLE_GAP = 20.0  # "Total Weight" line sits this far above the grid top


def _make_rows(rng: random.Random, n: int) -> list[dict]:
    rows = []
    item = rng.randint(700, 850)
    for _ in range(n):
        profile, kg_per_m = rng.choice(PROFILES)
        qty = rng.randint(1, 8)
        unit_len = rng.choice([500, 620, 743, 900, 1052, 1357, 2077, 2864, 3099, 5842])
        unit_len += rng.randint(0, 40)  # break the round numbers a little
        total_len = qty * unit_len
        weight = round(total_len / 1000.0 * kg_per_m, 1)
        rows.append(
            {
                "item_no": str(item),
                "qty": str(qty),
                "desc": rng.choice(DESCRIPTIONS),
                "profile": profile,
                "unit": str(unit_len),
                "total": str(total_len),
                "weight": f"{weight:.1f}",
                "notes": "WELDED AS MARKED" if rng.random() < 0.15 else "",
            }
        )
        item += 1
    return rows


def _draw_table(page: fitz.Page, rows: list[dict], title: str) -> None:
    col_edges = [MARGIN]
    for _, w in COLUMNS:
        col_edges.append(col_edges[-1] + w)
    x0, x1 = col_edges[0], col_edges[-1]

    grid_top = MARGIN + 46.0  # leave room above for the title + total-weight line
    row_edges = [grid_top, grid_top + HEADER_H]
    for _ in rows:
        row_edges.append(row_edges[-1] + ROW_H)
    y0, y1 = row_edges[0], row_edges[-1]

    black = (0, 0, 0)

    # --- ruling: every row edge and every column edge, full extent
    for y in row_edges:
        page.draw_line((x0, y), (x1, y), color=black, width=RULE_W)
    for x in col_edges:
        page.draw_line((x, y0), (x, y1), color=black, width=RULE_W)

    def put(text: str, cell_x0: float, cell_y0: float, cell_h: float, size: float) -> None:
        if not text:
            return
        page.insert_text(
            (cell_x0 + CELL_INSET, cell_y0 + cell_h * 0.68),
            text,
            fontsize=size,
            fontname="helv",
            color=black,
        )

    # --- header row
    for c, (label, _) in enumerate(COLUMNS):
        put(label, col_edges[c], row_edges[0], HEADER_H, 8.5)

    # --- data rows
    keys = [key for _, key in _COL_KEYS]
    for i, row in enumerate(rows):
        ry0 = row_edges[i + 1]
        for c, key in enumerate(keys):
            put(row[key], col_edges[c], ry0, ROW_H, 10.0)

    # --- title + the grand-total line the table-level checksum reads
    declared = round(sum(float(r["weight"]) for r in rows), 1)
    page.insert_text((x0, MARGIN + 12), title, fontsize=11, fontname="hebo", color=black)
    page.insert_text(
        (x0, grid_top - TITLE_GAP + 6),
        f"Total Weight: {declared:.1f} kg",
        fontsize=11,
        fontname="helv",
        color=black,
    )


# header label -> the row-dict key that fills that column
_COL_KEYS = [
    ("Item No", "item_no"),
    ("Qty", "qty"),
    ("Item Description", "desc"),
    ("Profile", "profile"),
    ("Unit Length [mm]", "unit"),
    ("Total Length [mm]", "total"),
    ("Total Weight [kg]", "weight"),
    ("Notes", "notes"),
]


def build_pdf(path: Path, rng: random.Random, n_rows: int, index: int) -> float:
    rows = _make_rows(rng, n_rows)
    width = COLUMNS_TOTAL_WIDTH + 2 * MARGIN
    height = MARGIN + 46.0 + HEADER_H + n_rows * ROW_H + MARGIN
    doc = fitz.open()
    page = doc.new_page(width=width, height=height)
    _draw_table(page, rows, f"SYNTHETIC BOM #{index} - TENSION POLE TMH2 G")
    doc.save(path)
    doc.close()
    return round(sum(float(r["weight"]) for r in rows), 1)


COLUMNS_TOTAL_WIDTH = sum(w for _, w in COLUMNS)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--count", type=int, default=5, help="how many PDFs to make")
    ap.add_argument("--rows", type=int, default=18, help="data rows per table")
    ap.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "synthetic_tables",
        help="output directory (default: <repo>/synthetic_tables)",
    )
    ap.add_argument("--seed", type=int, default=1, help="base RNG seed")
    args = ap.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    for i in range(1, args.count + 1):
        rng = random.Random(args.seed * 1000 + i)
        n = max(3, args.rows + rng.randint(-3, 3))
        path = args.out / f"synthetic_{i:02d}.pdf"
        total = build_pdf(path, rng, n, i)
        print(f"  {path.name}: {n} rows, grand total {total} kg")
    print(f"\n{args.count} PDF(s) in {args.out}")


if __name__ == "__main__":
    main()
