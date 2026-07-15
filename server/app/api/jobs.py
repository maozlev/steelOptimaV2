from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.documents import ensure_unlocked
from app.db.models import Cutout, Document, ExtractionJob, Page
from app.db.session import get_db
from app.extraction.service import create_job as create_extraction_job
from app.schemas.jobs import CutoutOut, JobCreateIn, JobOut
from app.workers.queue import worker

router = APIRouter(prefix="/api", tags=["jobs"])


def _job_out(db: Session, job: ExtractionJob) -> JobOut:
    out = JobOut.model_validate(job)
    out.cutout_count = (
        db.query(func.count(Cutout.id)).filter_by(job_id=job.id).scalar() or 0
    )
    return out


@router.post("/documents/{doc_id}/jobs", response_model=JobOut, status_code=202)
def create_job(
    doc_id: int, body: JobCreateIn | None = None, db: Session = Depends(get_db)
):
    doc = db.get(Document, doc_id)
    if not doc:
        raise HTTPException(404, "Document not found")
    ensure_unlocked(doc)
    params = {"vlm": body.vlm} if body and body.vlm is not None else None
    job = create_extraction_job(db, doc, params)
    worker.enqueue(job.id)
    return _job_out(db, job)


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: int, db: Session = Depends(get_db)):
    job = db.get(ExtractionJob, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return _job_out(db, job)


@router.delete("/jobs/{job_id}", response_model=JobOut)
def cancel_job(job_id: int, db: Session = Depends(get_db)):
    """Cancel a QUEUED job. The worker checks status when it dequeues, so a
    cancelled job is skipped for free. A running job cannot be cancelled — the
    OCR thread has no safe interruption point — and finished jobs are history."""
    job = db.get(ExtractionJob, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job.status != "queued":
        raise HTTPException(409, f"Job is {job.status}; only queued jobs cancel")
    job.status = "failed"
    job.error = "cancelled by user"
    db.commit()
    db.refresh(job)
    return _job_out(db, job)


@router.get("/pages/{page_id}/cutouts", response_model=list[CutoutOut])
def list_cutouts(page_id: int, db: Session = Depends(get_db)):
    if not db.get(Page, page_id):
        raise HTTPException(404, "Page not found")
    return (
        db.query(Cutout)
        .filter_by(page_id=page_id)
        .order_by(Cutout.confidence.desc())
        .all()
    )
