"""Build BOM PDFs whose right answer is known by construction.

Generated text is clean vector type, so nothing built here says anything about
OCR accuracy on real CAD stroke glyphs (see tools/make_synthetic_tables.py). What
it does give, for free and with no labelling, is a table whose every number we
chose — which is what the decision layer (validation, checksum boost, approve
gate) and the aggregation layer need in order to be testable at all.

Headers are chosen to hit the keyword map in app/tables/classify.py so the
VLM-off heuristic classifies the table "materials" with no model in the loop.
"""

import fitz

COLUMNS = [
    ("Item No", 46), ("Qty", 34), ("Item Description", 118), ("Profile", 96),
    ("Unit Length [mm]", 80), ("Total Length [mm]", 84), ("Total Weight [kg]", 84),
]
KEYS = ["item_no", "qty", "desc", "profile", "unit", "total", "weight"]
MARGIN, ROW_H, HEADER_H, GRID_TOP_GAP = 44.0, 22.0, 26.0, 46.0
KG_PER_M = 5.42  # L60x60x6


def bom_row(item: int, qty: int, unit_len: int, profile: str = "L60x60x6") -> dict:
    """A row whose arithmetic holds exactly: total = qty x unit, weight from it."""
    total = qty * unit_len
    return {
        "item_no": str(item), "qty": str(qty), "desc": "Diagonal",
        "profile": profile, "unit": str(unit_len), "total": str(total),
        "weight": f"{round(total / 1000.0 * KG_PER_M, 1):.1f}",
    }


def declared_total_of(rows: list[dict]) -> float:
    return round(sum(float(r["weight"]) for r in rows), 1)


def build_bom_pdf(path, rows: list[dict], declared_total: float | None = None) -> float:
    """Draw a ruled BOM plus the printed 'Total Weight: N kg' line the
    table-level checksum reads. Returns the printed total."""
    if declared_total is None:
        declared_total = declared_total_of(rows)
    col_edges = [MARGIN]
    for _, w in COLUMNS:
        col_edges.append(col_edges[-1] + w)
    x0, x1 = col_edges[0], col_edges[-1]
    grid_top = MARGIN + GRID_TOP_GAP
    row_edges = [grid_top, grid_top + HEADER_H]
    for _ in rows:
        row_edges.append(row_edges[-1] + ROW_H)

    doc = fitz.open()
    page = doc.new_page(width=x1 + MARGIN, height=row_edges[-1] + MARGIN)
    for y in row_edges:
        page.draw_line((x0, y), (x1, y), color=(0, 0, 0), width=0.8)
    for x in col_edges:
        page.draw_line((x, row_edges[0]), (x, row_edges[-1]), color=(0, 0, 0),
                       width=0.8)

    def put(text, cx, cy, ch, size):
        if text:
            page.insert_text((cx + 4.0, cy + ch * 0.68), text, fontsize=size,
                             fontname="helv", color=(0, 0, 0))

    for c, (label, _) in enumerate(COLUMNS):
        put(label, col_edges[c], row_edges[0], HEADER_H, 8.5)
    for i, row in enumerate(rows):
        for c, key in enumerate(KEYS):
            put(row[key], col_edges[c], row_edges[i + 1], ROW_H, 10.0)
    page.insert_text((x0, grid_top - 14), f"Total Weight: {declared_total:.1f} kg",
                     fontsize=11, fontname="helv", color=(0, 0, 0))
    doc.save(path)
    doc.close()
    return declared_total


def upload_bom(client, wait_job, tmp_path, project_id: int, name: str, rows,
               declared_total=None) -> dict:
    """Build -> upload into an existing project -> run the table job (VLM off)."""
    pdf = tmp_path / f"{name}.pdf"
    printed = build_bom_pdf(pdf, rows, declared_total)
    with open(pdf, "rb") as f:
        doc = client.post(f"/api/projects/{project_id}/documents",
                          files={"file": (pdf.name, f, "application/pdf")}).json()
    job = wait_job(client, client.post(f"/api/documents/{doc['id']}/table-jobs",
                                       json={"vlm": False}).json()["id"])
    assert job["status"] == "done", job["error"]
    tables = client.get(f"/api/documents/{doc['id']}/tables").json()
    bom = max(tables, key=lambda t: t["row_count"])
    assert bom["kind"] == "materials", "generated BOM did not classify — fixture bug"
    detail = client.get(f"/api/tables/{bom['id']}").json()
    return {"doc": doc, "table": bom, "rows": detail["rows"], "printed": printed}


def run_bom(client, wait_job, tmp_path, name: str, rows, declared_total=None) -> dict:
    """upload_bom into a fresh project of its own."""
    project_id = client.post("/api/projects", json={"name": name}).json()["id"]
    out = upload_bom(client, wait_job, tmp_path, project_id, name, rows,
                     declared_total)
    out["project_id"] = project_id
    return out


def by_item(rows: list[dict]) -> dict:
    """Rows keyed by the item number the pipeline read back."""
    return {(r["cells"][0]["value"] or "").strip(): r for r in rows}


def approve_everything(client, table_id: int) -> None:
    for row in client.get(f"/api/tables/{table_id}").json()["rows"]:
        if row["status"] == "needs_review":
            client.patch(f"/api/material-rows/{row['id']}", json={"action": "approve"})
    r = client.patch(f"/api/tables/{table_id}", json={"action": "approve"})
    assert r.status_code == 200, r.text
