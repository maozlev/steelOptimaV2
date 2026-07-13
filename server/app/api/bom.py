from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.bom.service import ACCEPTED_STATUSES, build_rows, totals
from app.db.models import Cutout, Document, Page
from app.db.session import get_db

router = APIRouter(prefix="/api", tags=["bom"])


def _document_cutouts(db: Session, doc_id: int) -> list[Cutout]:
    page_ids = db.query(Page.id).filter(Page.document_id == doc_id)
    return db.query(Cutout).filter(Cutout.page_id.in_(page_ids)).order_by(Cutout.id).all()


@router.get("/documents/{doc_id}/bom")
def document_bom(doc_id: int, db: Session = Depends(get_db)):
    """BOM for the whole document — every page, not just the one on screen."""
    doc = db.get(Document, doc_id)
    if not doc:
        raise HTTPException(404, "Document not found")

    rows = build_rows(_document_cutouts(db, doc_id))
    return {
        "document": {"id": doc.id, "filename": doc.filename, "status": doc.status},
        "rows": rows,
        "totals": totals(rows),
    }


@router.get("/bom/aggregate")
def aggregate_bom(db: Session = Depends(get_db)):
    """Combined BOM across every approved document.

    Only approved documents contribute, and within them only accepted cutouts —
    a pending document is still being reviewed and its numbers are not final.
    """
    docs = (
        db.query(Document)
        .filter(Document.status == "approved")
        .order_by(Document.id)
        .all()
    )

    # Pool every accepted cutout and group once, rather than merging per-document
    # rows: a merged row would inherit the first document's mean size while its
    # cut length summed across all of them, and the two would stop reconciling.
    pooled: list[Cutout] = []
    sources: dict[str, list[str]] = {}
    for doc in docs:
        accepted = [
            c for c in _document_cutouts(db, doc.id) if c.status in ACCEPTED_STATUSES
        ]
        pooled += accepted
        for row in build_rows(accepted):
            sources.setdefault(row["key"], []).append(doc.filename)

    rows = build_rows(pooled)
    for row in rows:
        row["documents"] = sources.get(row["key"], [])

    return {
        "documents": [{"id": d.id, "filename": d.filename} for d in docs],
        "rows": rows,
        "totals": totals(rows),
    }
