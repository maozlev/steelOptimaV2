"""Table classification: what kind of table is this grid, and what is each column?

One VLM call per table, on a crop of the table plus padding (the NCD BOM's header
row sits BELOW and outside the detected grid — the padding is what lets the model
see it). When the VLM is off or down, a deterministic header-keyword heuristic
still assigns roles for English tables; Hebrew headers are beyond the OCR model,
so those tables come back "unknown" and every row lands in review — honest, not
silently wrong.
"""

import json
import re
from dataclasses import dataclass, field

import fitz

from app.tables.cells import read_strip_text
from app.tables.grid import TableGrid
from app.tables.normalize import fix_homoglyphs, parse_number
from app.tables.regions import render_region
from app.vlm.client import OllamaVlmClient
from app.vlm.prompts import (
    TABLE_VERDICT_SCHEMA,
    TableVerdict,
    classify_table_prompt,
)

CLASSIFY_PAD_ROWS = 3  # rows of context above/below the grid in the VLM crop
CLASSIFY_DPI = 300
CLASSIFY_MAX_PX = 1024  # bigger crops put a 9B vision model into minutes-per-call

# header keyword -> column role, matched on OCR'd header cells (case-insensitive,
# substring). Ordered: first hit wins, more specific phrases first.
_HEADER_KEYWORDS: list[tuple[str, str]] = [
    ("total length", "total_length"),
    ("total weight", "total_weight"),
    ("unit weight", "unit_weight"),
    ("unit length", "unit_length"),
    ("item description", "description"),
    ("description", "description"),
    ("item number", "item_no"),
    ("item", "item_no"),
    ("qty", "qty"),
    ("quantity", "qty"),
    ("profile", "profile"),
    ("section", "profile"),
    ("diameter", "diameter"),
    ("dia.", "diameter"),
    ("length", "unit_length"),
    ("weight", "total_weight"),
    ("level", "level"),
    ("notes", "other"),
]


# The words that say "this is the table we need" (Maoz). A grid whose readable
# header/context text contains none of these is not a materials table and never
# earns a VLM call; one that contains them is processed even with the VLM down.
MATERIAL_MARKERS = (
    "weight", "kg", "mm", "cm", "length", "total", "qty", "quantity",
    "pcs", "dia", "profile", "section", "size",
    "משקל", "אורך", "קוטר", "כמות", "מידה", "פרופיל", 'סה"כ',
)


def has_material_markers(texts: list[str]) -> bool:
    joined = fix_homoglyphs(" ".join(t for t in texts if t)).lower()
    return any(marker in joined for marker in MATERIAL_MARKERS)


@dataclass
class TableClassification:
    kind: str = "unknown"
    title: str = ""
    column_roles: list[str] = field(default_factory=list)
    header_rows: int = 1
    header_position: str = "top"
    length_unit: str = "mm"
    confidence: float = 0.0
    source: str = "heuristic"  # vlm | heuristic


def data_row_indices(grid: TableGrid, cls: TableClassification) -> list[int]:
    rows = list(range(grid.n_rows))
    h = min(cls.header_rows, grid.n_rows - 1)
    if h <= 0:
        return rows
    return rows[h:] if cls.header_position == "top" else rows[:-h]


def _classify_crop_bbox(page: fitz.Page, grid: TableGrid) -> tuple:
    heights = [b - a for a, b in zip(grid.row_edges, grid.row_edges[1:])]
    med = sorted(heights)[len(heights) // 2]
    pad = CLASSIFY_PAD_ROWS * med
    x0, y0, x1, y1 = grid.bbox
    return (x0, y0 - pad, x1, y1 + pad)


def classify_with_vlm(
    page: fitz.Page, grid: TableGrid, client: OllamaVlmClient
) -> tuple[TableClassification | None, "object"]:
    """Returns (classification, VlmJsonResult) — the raw result feeds the audit row."""
    png = render_region(
        page, _classify_crop_bbox(page, grid), dpi=CLASSIFY_DPI, max_px=CLASSIFY_MAX_PX
    )

    def _validate(data: dict) -> None:
        verdict = TableVerdict.model_validate(data)
        if len(verdict.column_roles) != grid.n_cols:
            raise ValueError(
                f"expected {grid.n_cols} column roles, got {len(verdict.column_roles)}"
            )

    result = client.chat_json(
        png, classify_table_prompt(grid.n_cols), TABLE_VERDICT_SCHEMA, validate=_validate
    )
    if not result.ok:
        return None, result
    v = TableVerdict.model_validate(result.data)
    return (
        TableClassification(
            kind=v.kind,
            title=v.title,
            column_roles=v.column_roles,
            header_rows=v.header_rows,
            header_position=v.header_position,
            length_unit=v.length_unit,
            confidence=v.confidence,
            source="vlm",
        ),
        result,
    )


def classify_heuristic(
    candidates: list[tuple[str, int, list[str]]],
) -> TableClassification:
    """Deterministic fallback from OCR'd candidate header rows.

    candidates: (header_position, header_rows, per-column texts). header_rows is 0
    for a heading strip OUTSIDE the grid (the NCD BOM prints its header below the
    ruled rows), 1 for a grid row serving as the header."""
    best = TableClassification()
    n_cols = len(candidates[0][2]) if candidates else 0
    best_known = -1
    for position, header_rows, texts in candidates:
        roles = []
        for cell_text in texts:
            text = fix_homoglyphs(cell_text or "").lower()
            role = "other"
            for keyword, mapped in _HEADER_KEYWORDS:
                if keyword in text:
                    role = mapped
                    break
            roles.append(role)
        known = sum(1 for r in roles if r != "other")
        if known > best_known:
            best_known = known
            has_qty = "qty" in roles
            has_dim = any(
                r in ("unit_length", "total_length", "diameter", "profile")
                for r in roles
            )
            best = TableClassification(
                kind="materials" if has_qty and has_dim else "unknown",
                column_roles=roles,
                header_rows=header_rows,
                header_position=position,
                confidence=0.5 if known >= 3 else 0.2,
                source="heuristic",
            )
    if not best.column_roles:
        best.column_roles = ["other"] * n_cols
    return best


_WEIGHT_LINE_RE = re.compile(r"total\s*weight\D{0,3}(\d[\d.,]*)", re.IGNORECASE)


def read_declared_total_weight(
    page: fitz.Page, grid: TableGrid, dpi: int
) -> float | None:
    """'Total Weight: 3814.4 kg' printed above or below the table.

    Tried at two strip heights: the tight strip reads the line clean; the taller
    one is a fallback when the note sits a full row away from the table edge."""
    heights = [b - a for a, b in zip(grid.row_edges, grid.row_edges[1:])]
    med = sorted(heights)[len(heights) // 2]
    x0, y0, x1, y1 = grid.bbox
    for factor in (1.2, 2.2):
        strips = (
            (x0, y0 - factor * med, x1, y0 - 0.1),
            (x0, y1 + 0.1, x1, y1 + factor * med),
        )
        for strip in strips:
            for line in read_strip_text(page, strip, dpi):
                m = _WEIGHT_LINE_RE.search(fix_homoglyphs(line).replace(" ", ""))
                if m:
                    value = parse_number(m.group(1))
                    if value is not None:
                        return value
    return None


def classification_to_json(cls: TableClassification) -> str:
    return json.dumps(
        [{"index": i, "role": r} for i, r in enumerate(cls.column_roles)]
    )
