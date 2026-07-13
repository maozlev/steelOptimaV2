from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.documents import ensure_unlocked
from app.bom.service import ACCEPTED_STATUSES, build_rows, page_scales, totals
from app.db.models import Cutout, Document, Page
from app.db.session import get_db
from app.telemetry import tracker

router = APIRouter(prefix="/api", tags=["bom"])


def _document_cutouts(db: Session, doc_id: int) -> list[Cutout]:
    page_ids = db.query(Page.id).filter(Page.document_id == doc_id)
    return db.query(Cutout).filter(Cutout.page_id.in_(page_ids)).order_by(Cutout.id).all()


def _scale_status(db: Session, doc_id: int) -> dict:
    """Whether this document's sizes can be believed.

    Dimensions are measured in PAPER mm and multiplied by the sheet scale. If a page's
    scale is unknown or unverified, its numbers are the size of ink on a page, not of a
    part — and the operator has to be told so, loudly.
    """
    pages = db.query(Page).filter(Page.document_id == doc_id).order_by(Page.index).all()
    return {
        "pages": [
            {
                "page_index": p.index,
                "page_id": p.id,
                "scale": p.scale,
                "confident": p.scale_confident,
                "note": p.scale_note,
            }
            for p in pages
        ],
        "trustworthy": all(p.scale is not None and p.scale_confident for p in pages),
    }


@router.get("/documents/{doc_id}/bom")
def document_bom(doc_id: int, db: Session = Depends(get_db)):
    """BOM for the whole document — every page, not just the one on screen."""
    doc = db.get(Document, doc_id)
    if not doc:
        raise HTTPException(404, "Document not found")

    cutouts = _document_cutouts(db, doc_id)
    rows = build_rows(cutouts, page_scales(db, cutouts))
    return {
        "document": {"id": doc.id, "filename": doc.filename, "status": doc.status},
        "scale": _scale_status(db, doc_id),
        "rows": rows,
        "totals": totals(rows),
    }


class PageScaleIn(BaseModel):
    # real_mm / paper_mm: a 1:5 sheet is 5.0, a 2:1 magnified sheet is 0.5
    scale: float = Field(gt=0, le=1000)
    session_id: str | None = None


@router.patch("/pages/{page_id}/scale")
def set_page_scale(
    page_id: int, body: PageScaleIn, db: Session = Depends(get_db)
):
    """Let the operator state the scale the system could not establish.

    Some sheets carry no printed scale and no dimension the geometry can be checked
    against — A (3) is one. Guessing there would silently produce wrong parts; asking
    is a five-second job. A scale set by a human is trusted.
    """
    page = db.get(Page, page_id)
    if not page:
        raise HTTPException(404, "Page not found")
    doc = db.get(Document, page.document_id)
    ensure_unlocked(doc)

    page.scale = body.scale
    page.scale_confident = True
    page.scale_note = "set by operator"
    tracker.emit(
        db, "page_scale_set", entity_id=page_id,
        payload={"scale": body.scale}, session_id=body.session_id,
    )
    db.commit()
    return {
        "page_id": page.id,
        "scale": page.scale,
        "confident": page.scale_confident,
        "note": page.scale_note,
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
    untrusted: list[str] = []
    for doc in docs:
        accepted = [
            c for c in _document_cutouts(db, doc.id) if c.status in ACCEPTED_STATUSES
        ]
        if not _scale_status(db, doc.id)["trustworthy"]:
            untrusted.append(doc.filename)
        pooled += accepted
        scales = page_scales(db, accepted)
        for row in build_rows(accepted, scales):
            sources.setdefault(row["key"], []).append(doc.filename)

    rows = build_rows(pooled, page_scales(db, pooled))
    for row in rows:
        row["documents"] = sources.get(row["key"], [])

    return {
        "documents": [{"id": d.id, "filename": d.filename} for d in docs],
        # a roll-up that silently mixes real millimetres with paper ones is worse than
        # no roll-up: name the documents whose scale is not established
        "untrusted_scale": untrusted,
        "rows": rows,
        "totals": totals(rows),
    }
