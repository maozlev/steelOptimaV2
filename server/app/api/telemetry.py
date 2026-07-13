from collections import Counter

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import Cutout, Document, Page, VlmCall
from app.db.session import get_db
from app.schemas.telemetry import TelemetryBatchIn
from app.telemetry import tracker

router = APIRouter(prefix="/api/telemetry", tags=["telemetry"])

CONFIDENCE_BUCKETS = [(0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.0)]


@router.post("/events", status_code=202)
def post_events(batch: TelemetryBatchIn, db: Session = Depends(get_db)):
    for event in batch.events:
        tracker.emit(
            db,
            event.type,
            entity_id=event.entity_id,
            payload=event.payload,
            session_id=batch.session_id,
        )
    db.commit()
    return {"accepted": len(batch.events)}


_STATUSES = ("pending", "approved", "rejected", "edited")


def _status_stats(rows: list[tuple[str, int]]) -> dict:
    counts = dict.fromkeys(_STATUSES, 0)
    counts.update({status: n for status, n in rows})
    reviewed = counts["approved"] + counts["rejected"] + counts["edited"]
    accepted = counts["approved"] + counts["edited"]
    return {
        **counts,
        "reviewed": reviewed,
        # total includes pending; approve_rate deliberately does not, so a
        # summary with untouched cutouts can read "100%" — always show both
        "total": reviewed + counts["pending"],
        "approve_rate": round(accepted / reviewed, 4) if reviewed else None,
    }


@router.get("/summary")
def telemetry_summary(document_id: int | None = None, db: Session = Depends(get_db)):
    """Cutout review stats. Scoped to one document when document_id is given,
    otherwise across every document in the database."""
    cutouts = db.query(Cutout.status, Cutout.source, Cutout.confidence)
    vlm_calls = db.query(VlmCall)
    if document_id is not None:
        if not db.get(Document, document_id):
            raise HTTPException(404, "Document not found")
        page_ids = db.query(Page.id).filter(Page.document_id == document_id)
        cutouts = cutouts.filter(Cutout.page_id.in_(page_ids))
        vlm_calls = vlm_calls.filter(
            VlmCall.cutout_id.in_(
                db.query(Cutout.id).filter(Cutout.page_id.in_(page_ids))
            )
        )

    rows = cutouts.all()

    by_source = {}
    for source in ("vector", "raster_cv", "vlm", "fused", "manual"):
        counts = Counter(r.status for r in rows if r.source == source)
        if counts:
            by_source[source] = _status_stats(list(counts.items()))

    by_confidence = []
    for lo, hi in CONFIDENCE_BUCKETS:
        in_bucket = (
            (lambda c: lo <= c <= hi) if hi >= 1.0 else (lambda c: lo <= c < hi)
        )
        counts = Counter(r.status for r in rows if in_bucket(r.confidence))
        by_confidence.append(
            {"bucket": f"{lo:.1f}-{hi:.1f}", **_status_stats(list(counts.items()))}
        )

    calls, ok_calls, avg_latency = vlm_calls.with_entities(
        func.count(VlmCall.id),
        func.sum(case((VlmCall.ok, 1), else_=0)),
        func.avg(VlmCall.latency_ms),
    ).one()
    return {
        "document_id": document_id,
        "escalation_threshold": settings.escalation_threshold,
        "by_source": by_source,
        "by_confidence": by_confidence,
        "vlm": {
            "calls": calls or 0,
            "ok_rate": round((ok_calls or 0) / calls, 4) if calls else None,
            "avg_latency_ms": round(avg_latency, 1) if avg_latency else None,
        },
    }
