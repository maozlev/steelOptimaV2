"""Turn an attached drawing into PROPOSED plan items.

Two readers, than which nothing runs unconfirmed:

- PDF: the deterministic table path — detect ruled grids, OCR the cells,
  keep rows that look like material lines. A lean, DB-free sibling of
  tables/service.py's job pipeline: this feeds a proposal list a human
  approves item by item, so it deliberately skips the VLM repair loop and
  arithmetic validation the reviewed pipeline earns its keep with.
- Image (PNG/JPEG, incl. whiteboard sketches): the local VLM with a strict
  JSON schema. The VLM is NOT trusted with numbers anywhere else in this
  project, and this is no exception — its output is only ever a proposal
  card the user must click to accept.

Every item carries `source` so the UI can say where a number came from.
"""

import fitz

from app.config import settings
from app.tables import cells as cells_mod
from app.tables.classify import classify_heuristic, data_row_indices
from app.tables.grid import detect_grids
from app.tables.normalize import (
    canonical_material_key,
    parse_area,
    parse_number,
    parse_plate,
    parse_thk,
)
from app.vlm.client import OllamaVlmClient

MAX_PAGES = 10  # a tender PDF can be huge; proposals only need the front

ITEMS_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["bar", "plate"]},
                    "material": {"type": "string"},
                    "qty": {"type": "integer"},
                    "unit_length_mm": {"type": ["number", "null"]},
                    "thk_mm": {"type": ["number", "null"]},
                    "w_mm": {"type": ["number", "null"]},
                    "h_mm": {"type": ["number", "null"]},
                },
                "required": ["kind", "material", "qty"],
            },
        },
        "notes": {"type": "string"},
    },
    "required": ["items"],
}

VLM_PROMPT = """\
This image is a steel-construction drawing or a hand sketch of parts to build.
List every part it asks for. For each part give:
- kind: "bar" (profile cut to length: L/U/IPE/RHS angles, beams, tubes) or "plate"
- material: the profile designation exactly as written (e.g. "L60x60x6", "PLATE 12mm")
- qty: how many pieces
- unit_length_mm for bars; thk_mm + w_mm + h_mm for plates
Read ONLY what is written. If a quantity or size is not written, do not guess —
omit the item and mention it in "notes". Answer in the JSON schema given."""


def _grid_items(page: fitz.Page, grid) -> list[dict]:
    """OCR one ruled grid; return material-looking rows as proposal items."""
    dpi = settings.table_ocr_dpi
    image = cells_mod.TableImage(page, grid, dpi)
    first = [cells_mod.ocr_cell(image.cell_image(0, c)) for c in range(grid.n_cols)]
    last = [
        cells_mod.ocr_cell(image.cell_image(grid.n_rows - 1, c))
        for c in range(grid.n_cols)
    ]

    # the header may sit OUTSIDE the grid (the NCD BOM prints it below the
    # ruled rows) — OCR the strips just above/below too, as the job pipeline does
    from app.tables.grid import TableGrid

    heights = [b - a for a, b in zip(grid.row_edges, grid.row_edges[1:])]
    med = sorted(heights)[len(heights) // 2]

    def _strip_reads(above: bool) -> list[cells_mod.CellRead]:
        y0, y1 = (
            (grid.bbox[1] - 1.9 * med, grid.bbox[1] - 0.05)
            if above
            else (grid.bbox[3] + 0.05, grid.bbox[3] + 1.9 * med)
        )
        if y1 <= y0:
            return [cells_mod.CellRead() for _ in range(grid.n_cols)]
        strip = TableGrid(
            bbox=(grid.bbox[0], y0, grid.bbox[2], y1),
            col_edges=grid.col_edges,
            row_edges=[y0, y1],
        )
        strip_image = cells_mod.TableImage(page, strip, dpi)
        return [
            cells_mod.ocr_cell(strip_image.cell_image(0, c))
            for c in range(grid.n_cols)
        ]

    texts = lambda reads: [c.value or "" for c in reads]  # noqa: E731
    cls = classify_heuristic(
        [
            ("top", 1, texts(first)),
            ("bottom", 1, texts(last)),
            ("top", 0, texts(_strip_reads(above=True))),
            ("bottom", 0, texts(_strip_reads(above=False))),
        ]
    )
    if cls.kind != "materials":
        return []

    items: list[dict] = []
    for r in data_row_indices(grid, cls):
        reads = (
            first
            if r == 0
            else last
            if r == grid.n_rows - 1
            else [cells_mod.ocr_cell(image.cell_image(r, c)) for c in range(grid.n_cols)]
        )
        values: dict[str, str | None] = {}
        for role, cell in zip(cls.column_roles, reads):
            if role != "other" and role not in values:
                values[role] = cell.value
        qty = parse_number(values.get("qty"))
        if not qty or qty <= 0:
            continue
        key = canonical_material_key(values)
        if not key:
            continue
        unit_len = parse_number(values.get("unit_length"))
        plate = parse_plate(values.get("unit_length")) if unit_len is None else None
        item = {
            "material_key": key,
            "qty": int(qty),
            "unit_length_mm": unit_len,
            "thk_mm": parse_thk(values.get("description")),
            "w_mm": plate[0] if plate else None,
            "h_mm": plate[1] if plate else None,
            "area_m2": parse_area(values.get("total_length")),
            "source": "table_ocr",
        }
        items.append(item)
    return items


def analyze_pdf(data: bytes) -> dict:
    items: list[dict] = []
    warnings: list[str] = []
    with fitz.open(stream=data, filetype="pdf") as pdf:
        pages = min(len(pdf), MAX_PAGES)
        if len(pdf) > MAX_PAGES:
            warnings.append(f"read first {MAX_PAGES} of {len(pdf)} pages")
        for i in range(pages):
            page = pdf[i]
            for grid in detect_grids(page):
                items.extend(_grid_items(page, grid))
    if not items:
        warnings.append(
            "no material table found in the PDF — if it's a scanned image, "
            "attach it as PNG/JPEG instead"
        )
    return {"items": items, "source": "table_ocr", "warnings": warnings}


def analyze_image(data: bytes, client: OllamaVlmClient) -> dict:
    result = client.chat_json(data, VLM_PROMPT, ITEMS_SCHEMA)
    if not result.ok:
        return {
            "items": [],
            "source": "vlm",
            "warnings": [f"the vision model could not read the image: {result.error}"],
        }
    items: list[dict] = []
    for raw in result.data.get("items", []):
        qty = raw.get("qty")
        if not qty or qty <= 0:
            continue
        material = str(raw.get("material") or "").strip()
        if not material:
            continue
        key = canonical_material_key({"description": material}) or material.upper()
        items.append(
            {
                "material_key": key,
                "qty": int(qty),
                "unit_length_mm": raw.get("unit_length_mm"),
                "thk_mm": raw.get("thk_mm"),
                "w_mm": raw.get("w_mm"),
                "h_mm": raw.get("h_mm"),
                "area_m2": None,
                "source": "vlm",
            }
        )
    warnings = ["read by the vision model — VERIFY every number before adding"]
    notes = (result.data.get("notes") or "").strip()
    if notes:
        warnings.append(f"model notes: {notes}")
    return {"items": items, "source": "vlm", "warnings": warnings}
