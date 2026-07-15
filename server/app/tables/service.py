"""Material-table extraction job: detect grids, classify, read, validate, persist.

Mirrors extraction/service.py's job lifecycle (status transitions, WS emits,
graceful VLM degradation). The division of labour:

- deterministic: grid geometry, numeric OCR, arithmetic validation
- VLM (budgeted per table): classify the table + column roles (1 call),
  transcribe description columns the OCR can't read (Hebrew), and re-read rows
  that fail their own arithmetic (the repair loop)

A row auto-approves only when its checks pass and its cells are confident.
Everything else is flagged — a wrong value that is FLAGGED costs a click, an
unflagged one costs money.
"""

import json
from collections.abc import Callable
from datetime import UTC, datetime

import fitz
from sqlalchemy.orm import Session

from app.config import settings
from app.db import session as db_session
from app.db.models import Document, ExtractionJob, MaterialRow, MaterialTable, VlmCall
from app.tables import cells as cells_mod
from app.tables.classify import (
    TableClassification,
    classification_to_json,
    classify_heuristic,
    classify_with_vlm,
    data_row_indices,
    has_material_markers,
    read_declared_total_weight,
)
from app.tables.grid import TableGrid, detect_grids
from app.tables.normalize import (
    canonical_material_key,
    fix_homoglyphs,
    parse_number,
    to_mm,
)
from app.tables.regions import render_region
from app.tables.validate import row_status, validate_row, validate_table
from app.vlm.client import OllamaVlmClient

Emit = Callable[[dict], None]

NUMERIC_ROLES = {
    "item_no",
    "qty",
    "diameter",
    "unit_length",
    "total_length",
    "unit_weight",
    "total_weight",
    "level",
}
TEXT_ROLES = {"description", "profile"}


def create_table_job(
    db: Session, doc: Document, params: dict | None = None
) -> ExtractionJob:
    job = ExtractionJob(
        document_id=doc.id,
        status="queued",
        kind="tables",
        params_json=json.dumps(params or {}),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def _record_vlm_call(
    db: Session, job_id: int, table_id: int | None, trigger: str, result
) -> None:
    db.add(
        VlmCall(
            job_id=job_id,
            table_id=table_id,
            trigger=trigger,
            model=settings.vlm_model,
            prompt_hash=result.prompt_hash,
            latency_ms=result.latency_ms,
            response_json=result.raw_response,
            ok=result.ok,
        )
    )


def _normalized_fields(
    values: dict[str, str | None], length_unit: str
) -> dict[str, float | None]:
    """Column-role texts -> normalized numbers in mm/kg."""
    qty = parse_number(values.get("qty"))
    unit_len = parse_number(values.get("unit_length"))
    total_len = parse_number(values.get("total_length"))
    return {
        "qty": qty,
        "unit_length_mm": to_mm(unit_len, length_unit) if unit_len is not None else None,
        "total_length_mm": (
            to_mm(total_len, length_unit) if total_len is not None else None
        ),
        "unit_weight_kg": parse_number(values.get("unit_weight")),
        "total_weight_kg": parse_number(values.get("total_weight")),
    }


def _role_values(
    row_cells: list[cells_mod.CellRead], roles: list[str]
) -> dict[str, str | None]:
    values: dict[str, str | None] = {}
    for role, cell in zip(roles, row_cells):
        if role != "other" and role not in values:
            values[role] = cell.value
    return values


def _row_confidence(row_cells: list[cells_mod.CellRead], roles: list[str]) -> float:
    """The weakest meaningful cell bounds the row."""
    confs = []
    for role, cell in zip(roles, row_cells):
        if role == "other" or cell.source == "empty":
            continue
        if role in TEXT_ROLES and cell.source == "vlm":
            confs.append(0.9)  # a VLM transcription with nothing to check against
        else:
            confs.append(cell.ocr_conf if cell.source != "fused" else 0.98)
    return min(confs) if confs else 0.0


def _process_table(
    db: Session,
    job: ExtractionJob,
    page_row,
    page: fitz.Page,
    grid: TableGrid,
    client: OllamaVlmClient | None,
    emit: Emit,
) -> MaterialTable:
    dpi = settings.table_ocr_dpi
    vlm_budget = settings.table_vlm_max_calls

    table_row = MaterialTable(
        page_id=page_row.id,
        job_id=job.id,
        bbox=json.dumps(list(grid.bbox)),
        n_rows=grid.n_rows,
        n_cols=grid.n_cols,
    )
    db.add(table_row)
    db.flush()

    # OCR candidate header rows: first/last grid row, plus the strips just
    # above/below the grid (the NCD BOM's header is OUTSIDE the grid)
    image = cells_mod.TableImage(page, grid, dpi)

    def _grid_row_reads(r: int) -> list[cells_mod.CellRead]:
        return [
            cells_mod.ocr_cell(image.cell_image(r, c)) for c in range(grid.n_cols)
        ]

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

    candidates = [
        ("top", 1, _grid_row_reads(0)),
        ("bottom", 1, _grid_row_reads(grid.n_rows - 1)),
        ("top", 0, _strip_reads(above=True)),
        ("bottom", 0, _strip_reads(above=False)),
    ]
    texts_of = lambda reads: [c.value or "" for c in reads]  # noqa: E731
    first_texts = texts_of(candidates[0][2])
    last_texts = texts_of(candidates[1][2])
    heuristic = classify_heuristic(
        [(pos, hr, texts_of(reads)) for pos, hr, reads in candidates]
    )

    # --- the gate (Maoz): words like weight/kg/mm/length/total say "this is the
    # table we need". Grids whose readable context has none of them are junk and
    # never earn a VLM call; the VLM is only consulted when the OCR could not
    # read the header ink (Hebrew) and so cannot testify either way.
    all_reads = [c for _, _, reads in candidates for c in reads]
    inked = [c for c in all_reads if c.source != "empty"]
    read_ok = [c for c in inked if (c.value or "").strip()]
    readable = bool(inked) and len(read_ok) / len(inked) >= 0.5
    markers = has_material_markers([c.value or "" for c in all_reads])

    cls: TableClassification | None = None
    if heuristic.kind == "materials":
        cls = heuristic  # printed headers identified the table — no VLM needed
    elif markers:
        cls = heuristic  # marker words but roles unclear -> VLM may sharpen them
    elif readable or not inked:
        # header text was readable and contains none of the marker words:
        # a coordinate list, revision history or title block. Skip cheaply.
        cls = TableClassification(
            kind="other",
            column_roles=["other"] * grid.n_cols,
            header_rows=0,
            confidence=0.4,
            source="heuristic",
        )

    if cls is None or (cls.kind in ("unknown", "materials") and cls.confidence < 0.5):
        if client is not None:
            vlm_cls, vlm_result = classify_with_vlm(page, grid, client)
            _record_vlm_call(db, job.id, table_row.id, "table_classify", vlm_result)
            vlm_budget -= 1
            if vlm_cls is not None:
                cls = vlm_cls
        if cls is None:
            cls = heuristic

    if cls.header_rows > 0:
        # deterministic cross-check on the header claim: the NCD BOM prints its
        # header BELOW and OUTSIDE the grid, and a model looking at the padded
        # crop can't tell. A real header row is words; a data row is numbers —
        # if the claimed header row is mostly numeric, the header isn't in the grid
        claimed = first_texts if cls.header_position == "top" else last_texts
        filled = [t for t in claimed if t.strip()]
        numeric = sum(1 for t in filled if parse_number(t) is not None)
        if filled and numeric / len(filled) > 0.5:
            cls.header_rows = 0

    data_rows = data_row_indices(grid, cls)
    header_rows = grid.n_rows - len(data_rows)

    table_row.kind = cls.kind
    table_row.title = cls.title or None
    table_row.columns_json = classification_to_json(cls)
    table_row.header_rows = header_rows
    table_row.confidence = cls.confidence

    # cache the review/VLM crop
    crop_path = settings.table_crops_dir / f"{table_row.id}.png"
    crop_path.write_bytes(render_region(page, grid.bbox, dpi=200))
    table_row.crop_path = str(crop_path)

    if cls.kind not in ("materials", "unknown"):
        table_row.status = "rejected"  # not part of the BOM; visible, revivable
        db.flush()
        return table_row

    # ---- read every data cell (OCR) — reuse the render made for the heuristic
    matrix = [
        [cells_mod.ocr_cell(image.cell_image(r, c)) for c in range(grid.n_cols)]
        for r in data_rows
    ]
    roles = cls.column_roles

    # ---- VLM transcription for text columns the OCR can't read (Hebrew) or
    # low-confidence text cells
    if client is not None:
        for col, role in enumerate(roles):
            if role not in TEXT_ROLES:
                continue
            weak = [
                r
                for i, r in enumerate(data_rows)
                if matrix[i][col].source != "empty"
                and matrix[i][col].ocr_conf < settings.table_ocr_conf_threshold
            ]
            if not weak or vlm_budget <= 0:
                continue
            reads, calls = cells_mod.vlm_read_column(
                page, grid, weak, col, client, dpi=300
            )
            vlm_budget -= calls
            for i, r in enumerate(data_rows):
                if r in reads:
                    cell = matrix[i][col]
                    cell.vlm_value = reads[r]
                    if cell.raw_ocr and fix_homoglyphs(cell.raw_ocr) == fix_homoglyphs(
                        reads[r]
                    ):
                        cell.source = "fused"
                    else:
                        cell.value = reads[r]
                        cell.source = "vlm"

    # ---- normalize + validate, with one VLM repair pass for failed arithmetic
    declared_total = read_declared_total_weight(page, grid, dpi)
    table_row.declared_total_weight_kg = declared_total

    rows_payload = []
    for i, grid_row in enumerate(data_rows):
        values = _role_values(matrix[i], roles)
        fields = _normalized_fields(values, cls.length_unit)
        validation = validate_row(fields, roles)

        if (
            validation.flags
            and client is not None
            and vlm_budget > 0
            and any("mismatch" in f or "missing" in f for f in validation.flags)
        ):
            reread = cells_mod.vlm_read_row(page, grid, grid_row, client, dpi=300)
            vlm_budget -= 1
            if reread:
                for c, (cell, new_value) in enumerate(zip(matrix[i], reread)):
                    cell.vlm_value = new_value
                repaired_values = {
                    role: fix_homoglyphs(reread[c]) if reread[c] else values.get(role)
                    for c, role in enumerate(roles)
                    if role != "other"
                }
                repaired_fields = _normalized_fields(repaired_values, cls.length_unit)
                repaired_validation = validate_row(repaired_fields, roles)
                # accept the repair only when it makes the arithmetic hold
                if len(repaired_validation.flags) < len(validation.flags):
                    for c, cell in enumerate(matrix[i]):
                        if reread[c] and cell.value != reread[c]:
                            cell.value = reread[c]
                            cell.source = "vlm"
                    values, fields, validation = (
                        repaired_values,
                        repaired_fields,
                        repaired_validation,
                    )

        rows_payload.append((grid_row, matrix[i], values, fields, validation))

    # ---- table-level checksum boosts every participating row
    table_validation = validate_table(
        [fields for _, _, _, fields, _ in rows_payload], declared_total
    )
    table_row.validation_json = json.dumps(table_validation)
    weight_ok = table_validation.get("weight_total_matches")

    approved = flagged = 0
    for grid_row, row_cells, values, fields, validation in rows_payload:
        confidence = _row_confidence(row_cells, roles)
        if weight_ok and fields.get("total_weight_kg") is not None:
            # the printed grand total reconciles: the weight column is checksummed
            confidence = max(confidence, 0.95)
        if cls.kind == "unknown":
            status = "needs_review"
        else:
            status = row_status(
                validation, confidence, settings.table_row_approve_threshold
            )
        approved += status == "auto_approved"
        flagged += status != "auto_approved"
        db.add(
            MaterialRow(
                table_id=table_row.id,
                row_index=grid_row,
                cells_json=json.dumps(
                    [c.as_dict(col) for col, c in enumerate(row_cells)],
                    ensure_ascii=False,
                ),
                material_key=canonical_material_key(values),
                description=values.get("description"),
                qty=fields["qty"],
                unit_length_mm=fields["unit_length_mm"],
                total_length_mm=fields["total_length_mm"],
                unit_weight_kg=fields["unit_weight_kg"],
                total_weight_kg=fields["total_weight_kg"],
                flags_json=json.dumps(validation.flags),
                confidence=confidence,
                status=status,
            )
        )
    db.flush()

    emit(
        {
            "type": "table_done",
            "table_id": table_row.id,
            "page_index": page_row.index,
            "kind": table_row.kind,
            "rows": len(rows_payload),
            "auto_approved": approved,
            "needs_review": flagged,
        }
    )
    return table_row


def execute_table_job(job_id: int, emit: Emit = lambda e: None) -> None:
    with db_session.SessionLocal() as db:
        job = db.get(ExtractionJob, job_id)
        if job is None or job.status != "queued":
            return
        job.status = "running"
        job.started_at = datetime.now(UTC)
        db.commit()
        emit({"type": "job_started", "job_id": job_id})

        params = json.loads(job.params_json or "{}")
        vlm_on = params.get("vlm", settings.vlm_enabled and settings.table_vlm_enabled)
        client = OllamaVlmClient() if vlm_on else None
        if client is not None and not client.available():
            client = None
            emit({"type": "vlm_unavailable", "model": settings.vlm_model})

        doc = db.get(Document, job.document_id)
        try:
            with fitz.open(doc.path) as pdf:
                for page_row in doc.pages:
                    emit({"type": "page_started", "page_index": page_row.index})
                    page = pdf[page_row.index]
                    grids = detect_grids(page)
                    emit(
                        {
                            "type": "tables_found",
                            "page_index": page_row.index,
                            "count": len(grids),
                        }
                    )

                    # a re-run replaces prior automatic tables — unless a human
                    # already worked on their rows, in which case they are kept
                    stale = (
                        db.query(MaterialTable)
                        .filter(
                            MaterialTable.page_id == page_row.id,
                            MaterialTable.job_id.isnot(None),
                        )
                        .all()
                    )
                    kept_bboxes: list[tuple] = []
                    for t in stale:
                        touched = (
                            db.query(MaterialRow.id)
                            .filter(
                                MaterialRow.table_id == t.id,
                                MaterialRow.status.in_(
                                    ["approved", "rejected", "edited"]
                                ),
                            )
                            .first()
                        )
                        if touched or t.status == "approved":
                            kept_bboxes.append(tuple(json.loads(t.bbox)))
                            continue
                        db.query(VlmCall).filter(VlmCall.table_id == t.id).update(
                            {"table_id": None}, synchronize_session=False
                        )
                        db.query(MaterialRow).filter(
                            MaterialRow.table_id == t.id
                        ).delete(synchronize_session=False)
                        db.delete(t)
                    db.flush()

                    for grid in grids:
                        if any(
                            _bbox_iou(grid.bbox, kept) > 0.7 for kept in kept_bboxes
                        ):
                            continue  # human-reviewed table at this spot survives
                        _process_table(db, job, page_row, page, grid, client, emit)
                        # commit per TABLE, not per page: a page's VLM calls can
                        # take minutes, and an open write transaction that long
                        # locks every other writer out of SQLite (live telemetry
                        # inserts were failing while a job ran)
                        db.commit()
            job.status = "done"
        except Exception as e:
            job.status = "failed"
            job.error = f"{type(e).__name__}: {e}"

        job.finished_at = datetime.now(UTC)
        db.commit()
        emit({"type": f"job_{job.status}", "job_id": job_id, "error": job.error})


def _bbox_iou(a, b) -> float:
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inter = (ix1 - ix0) * (iy1 - iy0)
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    return inter / (area_a + area_b - inter)
