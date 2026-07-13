from pathlib import Path

import fitz
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models import Document, ExtractionJob, MaterialRow, MaterialTable, Page
from app.db.session import get_db
from app.schemas.jobs import JobCreateIn, JobOut
from app.schemas.tables import MaterialTableDetailOut, MaterialTableOut
from app.tables.service import create_table_job
from app.workers.queue import worker

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
