import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import fitz
from shapely.geometry import box as shapely_box
from sqlalchemy.orm import Session

from app.config import settings
from app.db import session as db_session
from app.db.models import Cutout, Document, ExtractionJob, VlmCall
from app.extraction.ocr import OcrWord, annotate_candidates, ocr_words_near
from app.extraction.raster import extract_raster_candidates
from app.extraction.scale import resolve_scale
from app.extraction.scoring import score_candidates
from app.extraction.vector import Candidate, extract_candidates
from app.telemetry import tracker
from app.vlm.client import OllamaVlmClient
from app.vlm.escalation import escalate_page
from app.vlm.verify import verify_page

Emit = Callable[[dict], None]


def _page_candidates(pdf: fitz.Document, page_row) -> list[Candidate]:
    page = pdf[page_row.index]
    if page_row.kind == "raster":
        cands = extract_raster_candidates(
            Path(page_row.render_path), page_row.render_dpi, abs(page.rect)
        )
        # raster pages have no embedded text — OCR the regions around candidates
        words = ocr_words_near(
            Path(page_row.render_path),
            page_row.render_dpi,
            [c.polygon.bounds for c in cands],
        )
    else:
        cands = extract_candidates(page)
        # cropped PDFs: get_drawings() can return paths outside the cropbox
        page_box = shapely_box(0, 0, page.rect.width, page.rect.height)
        cands = [c for c in cands if c.polygon.intersects(page_box)]
        words = [
            OcrWord(text=w[4], bbox=(w[0], w[1], w[2], w[3]))
            for w in page.get_text("words")
        ]
    annotate_candidates(cands, words)
    return cands


def create_job(db: Session, doc: Document, params: dict | None = None) -> ExtractionJob:
    job = ExtractionJob(
        document_id=doc.id, status="queued", params_json=json.dumps(params or {})
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def execute_job(job_id: int, emit: Emit = lambda e: None) -> None:
    with db_session.SessionLocal() as db:
        job = db.get(ExtractionJob, job_id)
        if job is None or job.status != "queued":
            return
        job.status = "running"
        job.started_at = datetime.now(UTC)
        tracker.emit(db, "job_started", entity_id=job_id)
        db.commit()
        emit({"type": "job_started", "job_id": job_id})

        params = json.loads(job.params_json or "{}")
        vlm_on = params.get("vlm", settings.vlm_enabled)
        client = OllamaVlmClient() if vlm_on else None
        if client is not None and not client.available():
            client = None
            # degrade gracefully: CV-only results, client learns why via WS
            emit({"type": "vlm_unavailable", "model": settings.vlm_model})

        doc = db.get(Document, job.document_id)
        try:
            with fitz.open(doc.path) as pdf:
                for page_row in doc.pages:
                    emit(
                        {
                            "type": "page_started",
                            "page_index": page_row.index,
                            "kind": page_row.kind,
                        }
                    )
                    cands = _page_candidates(pdf, page_row)
                    scores = score_candidates(cands)

                    # Recover real-world scale. Until this existed every dimension in
                    # the BOM was in PAPER mm: the gear's Ø290 bore was reported as
                    # Ø82.9 because its sheet is 1:3.5. An unconfident result is stored
                    # anyway but flagged, so the operator confirms it rather than the
                    # system silently cutting a part at the wrong size.
                    sc = resolve_scale(pdf[page_row.index], cands)
                    page_row.scale = sc.scale
                    page_row.scale_confident = sc.confident
                    page_row.scale_note = sc.note or None
                    emit(
                        {
                            "type": "page_scale",
                            "page_index": page_row.index,
                            "scale": sc.scale,
                            "confident": sc.confident,
                            "note": sc.note,
                        }
                    )
                    # a re-run replaces prior automatic detections; manual
                    # cutouts (job_id NULL) are user work and must survive
                    stale_ids = [
                        cid
                        for (cid,) in db.query(Cutout.id).filter(
                            Cutout.page_id == page_row.id, Cutout.job_id.isnot(None)
                        )
                    ]
                    if stale_ids:
                        db.query(VlmCall).filter(
                            VlmCall.cutout_id.in_(stale_ids)
                        ).update({"cutout_id": None}, synchronize_session=False)
                        db.query(Cutout).filter(Cutout.id.in_(stale_ids)).delete(
                            synchronize_session=False
                        )
                    rows = [
                        Cutout(
                            page_id=page_row.id,
                            job_id=job.id,
                            geometry_wkt=cand.polygon.wkt,
                            bbox=json.dumps(list(cand.polygon.bounds)),
                            kind=cand.kind,
                            source=cand.source,
                            confidence=confidence,
                            dimension_text=cand.dimension_text,
                            measured_dims_json=json.dumps(cand.measured_dims),
                        )
                        for cand, confidence in zip(cands, scores)
                    ]
                    db.add_all(rows)
                    db.flush()
                    vlm_calls = 0
                    if client is not None:
                        # rescue: review what the pipeline is UNSURE of
                        vlm_calls = escalate_page(
                            db, job.id, page_row, rows, cands, scores, emit, client
                        )
                        # veto: review what it is CONFIDENT of. That is where the errors
                        # the operator actually sees live — a GD&T frame scores 0.98.
                        if settings.vlm_verify:
                            vlm_calls += verify_page(
                                db, job.id, page_row, rows, cands, scores, emit, client
                            )
                    tracker.emit(
                        db,
                        "page_done",
                        entity_id=page_row.id,
                        payload={
                            "job_id": job.id,
                            "candidates": len(cands),
                            "vlm_calls": vlm_calls,
                        },
                    )
                    emit(
                        {
                            "type": "page_done",
                            "page_index": page_row.index,
                            "candidates": len(cands),
                            "vlm_calls": vlm_calls,
                        }
                    )
            job.status = "done"
        except Exception as e:
            job.status = "failed"
            job.error = f"{type(e).__name__}: {e}"

        job.finished_at = datetime.now(UTC)
        tracker.emit(
            db, f"job_{job.status}", entity_id=job_id, payload={"error": job.error}
        )
        db.commit()
        emit({"type": f"job_{job.status}", "job_id": job_id, "error": job.error})
