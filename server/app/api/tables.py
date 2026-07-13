from pathlib import Path

import fitz
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models import (
    Document,
    ExtractionJob,
    MaterialRow,
    MaterialTable,
    Page,
    Project,
)
from app.db.session import get_db
from app.schemas.jobs import JobCreateIn, JobOut
from app.schemas.tables import (
    MaterialRowOut,
    MaterialTableDetailOut,
    MaterialTableOut,
    RowPatchIn,
    TablePatchIn,
)
from app.tables.aggregate import project_summary
from app.tables.normalize import canonical_material_key
from app.tables.service import create_table_job
from app.tables.validate import validate_row
from app.telemetry import tracker
from app.workers.queue import worker

TABLE_KINDS = ("materials", "coordinates", "other", "unknown")

router = APIRouter(prefix="/api", tags=["tables"])


def _with_row_counts(db: Session, tables: list[MaterialTable]) -> list[MaterialTableOut]:
    counts: dict[int, dict[str, int]] = {}
    if tables:
        for table_id, status, count in (
            db.query(MaterialRow.table_id, MaterialRow.status, func.count())
            .filter(MaterialRow.table_id.in_([t.id for t in tables]))
            .group_by(MaterialRow.table_id, MaterialRow.status)
        ):
            counts.setdefault(table_id, {})[status] = count
    out = []
    for t in tables:
        by_status = counts.get(t.id, {})
        item = MaterialTableOut.model_validate(t)
        item.row_count = sum(by_status.values())
        item.needs_review_rows = by_status.get("needs_review", 0)
        item.auto_approved_rows = by_status.get("auto_approved", 0) + by_status.get(
            "approved", 0
        ) + by_status.get("edited", 0)
        out.append(item)
    return out


@router.get("/documents/{doc_id}/tables", response_model=list[MaterialTableOut])
def list_document_tables(doc_id: int, db: Session = Depends(get_db)):
    if not db.get(Document, doc_id):
        raise HTTPException(404, "Document not found")
    tables = (
        db.query(MaterialTable)
        .join(Page, MaterialTable.page_id == Page.id)
        .filter(Page.document_id == doc_id)
        .order_by(MaterialTable.id)
        .all()
    )
    return _with_row_counts(db, tables)


@router.get("/tables/{table_id}", response_model=MaterialTableDetailOut)
def get_table(table_id: int, db: Session = Depends(get_db)):
    table = db.get(MaterialTable, table_id)
    if not table:
        raise HTTPException(404, "Table not found")
    from app.schemas.tables import MaterialRowOut

    base = _with_row_counts(db, [table])[0]
    return MaterialTableDetailOut(
        **base.model_dump(),
        rows=[MaterialRowOut.model_validate(r) for r in table.rows],
    )


@router.patch("/tables/{table_id}", response_model=MaterialTableOut)
def patch_table(table_id: int, body: TablePatchIn, db: Session = Depends(get_db)):
    table = db.get(MaterialTable, table_id)
    if not table:
        raise HTTPException(404, "Table not found")

    if body.action == "approve":
        flagged = (
            db.query(MaterialRow.id)
            .filter(
                MaterialRow.table_id == table_id,
                MaterialRow.status == "needs_review",
            )
            .count()
        )
        if flagged:
            raise HTTPException(
                409, f"{flagged} rows still need review — approve or reject them first"
            )
        table.status = "approved"
    elif body.action == "reject":
        table.status = "rejected"
    elif body.action == "reopen":
        table.status = "pending"
    elif body.action == "set_kind":
        if body.kind not in TABLE_KINDS:
            raise HTTPException(422, f"kind must be one of {TABLE_KINDS}")
        table.kind = body.kind
        if table.status == "rejected" and body.kind in ("materials", "unknown"):
            table.status = "pending"  # a revived misclassification goes back to review
    else:
        raise HTTPException(422, "action must be approve, reject, reopen or set_kind")

    tracker.emit(db, f"table_{body.action}", entity_id=table_id)
    db.commit()
    db.refresh(table)
    return _with_row_counts(db, [table])[0]


@router.patch("/material-rows/{row_id}", response_model=MaterialRowOut)
def patch_row(row_id: int, body: RowPatchIn, db: Session = Depends(get_db)):
    row = db.get(MaterialRow, row_id)
    if not row:
        raise HTTPException(404, "Row not found")
    table = db.get(MaterialTable, row.table_id)
    if table.status == "approved":
        raise HTTPException(409, "Table is approved — reopen it to edit rows")

    if body.action == "approve":
        row.status = "approved"
    elif body.action == "reject":
        row.status = "rejected"
    elif body.action == "edit":
        if not body.fields:
            raise HTTPException(422, "edit requires fields")
        fields = body.fields
        for name in (
            "description",
            "qty",
            "unit_length_mm",
            "total_length_mm",
            "unit_weight_kg",
            "total_weight_kg",
        ):
            value = getattr(fields, name)
            if value is not None:
                setattr(row, name, value)
        # a human owns the row now: rebuild the key and re-check the arithmetic
        import json as _json

        roles = [c["role"] for c in _json.loads(table.columns_json or "[]")]
        row.material_key = canonical_material_key(
            {
                "description": row.description,
                "unit_length": (
                    str(row.unit_length_mm) if row.unit_length_mm is not None else None
                ),
            }
        )
        validation = validate_row(
            {
                "qty": row.qty,
                "unit_length_mm": row.unit_length_mm,
                "total_length_mm": row.total_length_mm,
                "unit_weight_kg": row.unit_weight_kg,
                "total_weight_kg": row.total_weight_kg,
            },
            roles,
        )
        row.flags_json = _json.dumps(validation.flags)
        row.confidence = 1.0
        row.status = "edited"
    else:
        raise HTTPException(422, "action must be approve, reject or edit")

    tracker.emit(db, f"material_row_{body.action}", entity_id=row_id)
    db.commit()
    db.refresh(row)
    return row


@router.get("/projects/{project_id}/summary")
def get_project_summary(project_id: int, db: Session = Depends(get_db)):
    if not db.get(Project, project_id):
        raise HTTPException(404, "Project not found")
    return project_summary(db, [project_id])


@router.get("/projects-summary")
def get_projects_summary(ids: str, db: Session = Depends(get_db)):
    """Cross-project rollup: ?ids=1,2,3."""
    try:
        project_ids = [int(x) for x in ids.split(",") if x.strip()]
    except ValueError:
        raise HTTPException(422, "ids must be a comma-separated list of integers")
    if not project_ids:
        raise HTTPException(422, "at least one project id required")
    return project_summary(db, project_ids)


@router.get("/tables/{table_id}/crop")
def get_table_crop(table_id: int, db: Session = Depends(get_db)):
    table = db.get(MaterialTable, table_id)
    if not table:
        raise HTTPException(404, "Table not found")
    if not table.crop_path or not Path(table.crop_path).exists():
        raise HTTPException(404, "Crop not rendered")
    return FileResponse(
        table.crop_path, media_type="image/png", headers={"Cache-Control": "no-store"}
    )


@router.post(
    "/documents/{doc_id}/table-jobs", response_model=JobOut, status_code=202
)
def create_document_table_job(
    doc_id: int, body: JobCreateIn | None = None, db: Session = Depends(get_db)
):
    doc = db.get(Document, doc_id)
    if not doc:
        raise HTTPException(404, "Document not found")
    pending = (
        db.query(ExtractionJob)
        .filter(
            ExtractionJob.document_id == doc_id,
            ExtractionJob.kind == "tables",
            ExtractionJob.status.in_(["queued", "running"]),
        )
        .first()
    )
    if pending:
        raise HTTPException(409, f"Table job {pending.id} already {pending.status}")
    params = {"vlm": body.vlm} if body and body.vlm is not None else None
    job = create_table_job(db, doc, params)
    worker.enqueue(job.id)
    return JobOut.model_validate(job)


@router.get("/pages/{page_id}/table-debug")
def table_debug(page_id: int, db: Session = Depends(get_db)):
    """Detected ruled grids on a page — geometry only, no OCR/VLM. Debug aid."""
    from app.tables.grid import detect_grids

    page_row = db.get(Page, page_id)
    if not page_row:
        raise HTTPException(404, "Page not found")
    doc = db.get(Document, page_row.document_id)
    with fitz.open(doc.path) as pdf:
        grids = detect_grids(pdf[page_row.index])
    return {
        "page_id": page_id,
        "grids": [
            {
                "bbox": list(g.bbox),
                "rows": g.n_rows,
                "cols": g.n_cols,
                "col_edges": g.col_edges,
                "row_edges": g.row_edges,
            }
            for g in grids
        ],
    }
