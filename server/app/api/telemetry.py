from fastapi import APIRouter, Depends
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import Cutout, VlmCall
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
        "approve_rate": round(accepted / reviewed, 4) if reviewed else None,
    }


@router.get("/summary")
def telemetry_summary(db: Session = Depends(get_db)):
    by_source = {}
    for source in ("vector", "raster_cv", "vlm", "fused", "manual"):
        rows = (
            db.query(Cutout.status, func.count(Cutout.id))
            .filter(Cutout.source == source)
            .group_by(Cutout.status)
            .all()
        )
        if rows:
            by_source[source] = _status_stats(rows)

    by_confidence = []
    for lo, hi in CONFIDENCE_BUCKETS:
        upper = Cutout.confidence <= hi if hi >= 1.0 else Cutout.confidence < hi
        rows = (
            db.query(Cutout.status, func.count(Cutout.id))
            .filter(Cutout.confidence >= lo, upper)
            .group_by(Cutout.status)
            .all()
        )
        by_confidence.append({"bucket": f"{lo:.1f}-{hi:.1f}", **_status_stats(rows)})

    calls, ok_calls, avg_latency = (
        db.query(
            func.count(VlmCall.id),
            func.sum(case((VlmCall.ok, 1), else_=0)),
            func.avg(VlmCall.latency_ms),
        ).one()
    )
    return {
        "escalation_threshold": settings.escalation_threshold,
        "by_source": by_source,
        "by_confidence": by_confidence,
        "vlm": {
            "calls": calls or 0,
            "ok_rate": round((ok_calls or 0) / calls, 4) if calls else None,
            "avg_latency_ms": round(avg_latency, 1) if avg_latency else None,
        },
    }
