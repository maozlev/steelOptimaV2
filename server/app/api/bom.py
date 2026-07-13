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


# The operator's scale and the drawing's own dimensions may differ by this much before
# it is treated as a disagreement worth shouting about.
SCALE_DISAGREEMENT = 0.05


def _disagreement(page: Page) -> str | None:
    """Does the scale the operator set contradict what the drawing says?

    This is the ONLY thing that can catch a typo. The operator owns the number — but
    "1:50" for a 1:5 sheet cuts every part ten times too big, and does it silently, which
    is exactly the failure mode the whole scale system exists to prevent. Two independent
    sources still have to agree; only the roles have swapped. The detector no longer
    decides — it checks.
    """
    if not page.scale or not page.scale_detected:
        return None
    a, b = page.scale, page.scale_detected
    if abs(a - b) <= SCALE_DISAGREEMENT * max(a, b):
        return None
    return (
        f"you set 1:{a:g}, but this drawing's own dimensions say 1:{b:g} "
        f"— that is {max(a, b) / min(a, b):.1f}x out. One of them is wrong."
    )


def _scale_status(db: Session, doc_id: int) -> dict:
    """Whether this document's sizes can be believed.

    Dimensions are measured in PAPER mm and multiplied by the sheet scale. Until a human
    has confirmed that scale, the numbers are the size of ink on a page, not of a part.
    """
    pages = db.query(Page).filter(Page.document_id == doc_id).order_by(Page.index).all()
    return {
        "pages": [
            {
                "page_index": p.index,
                "page_id": p.id,
                "scale": p.scale,
                "detected": p.scale_detected,
                "confirmed": p.scale_confirmed,
                "confident": p.scale_confident,
                "disagreement": _disagreement(p),
                "note": p.scale_note,
            }
            for p in pages
        ],
        # the operator has signed off on every page
        "trustworthy": all(p.scale is not None and p.scale_confirmed for p in pages),
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
def set_page_scale(page_id: int, body: PageScaleIn, db: Session = Depends(get_db)):
    """The operator sets the sheet scale. This is THEIR call, and the document cannot be
    finalized until they have made it.

    The detector's estimate is kept (scale_detected) and cross-checked against what they
    typed. It no longer decides — it checks. That check is the only thing standing between
    a mistyped "1:50" on a 1:5 sheet and every part being cut ten times too big.
    """
    page = db.get(Page, page_id)
    if not page:
        raise HTTPException(404, "Page not found")
    doc = db.get(Document, page.document_id)
    ensure_unlocked(doc)

    page.scale = body.scale
    page.scale_confirmed = True
    page.scale_note = "confirmed by operator"
    warning = _disagreement(page)

    tracker.emit(
        db,
        "page_scale_set",
        entity_id=page_id,
        payload={
            "scale": body.scale,
            "detected": page.scale_detected,
            "disagrees": bool(warning),
        },
        session_id=body.session_id,
    )
    db.commit()
    return {
        "page_id": page.id,
        "scale": page.scale,
        "detected": page.scale_detected,
        "confirmed": page.scale_confirmed,
        "confident": page.scale_confident,
        # accepted, but the operator is told plainly that the drawing disagrees
        "disagreement": warning,
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
