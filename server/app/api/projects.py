from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models import (
    Document,
    ExtractionJob,
    MaterialPrice,
    MaterialRow,
    MaterialTable,
    OrderPlan,
    Page,
    Project,
)
from app.config import settings
from app.db.session import get_db
from app.ingestion.service import DuplicateDocumentError, ingest_document
from app.schemas.jobs import JobOut
from app.tables.service import create_table_job
from app.workers.queue import worker
from app.schemas.documents import DocumentDetailOut
from app.schemas.projects import (
    ProjectDetailOut,
    ProjectDocumentOut,
    ProjectIn,
    ProjectListOut,
    ProjectOut,
    ProjectPatchIn,
)
from app.telemetry import tracker

router = APIRouter(prefix="/api", tags=["projects"])

ALLOWED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png"}


def _get_project(db: Session, project_id: int) -> Project:
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    return project


def _doc_table_stats(db: Session, doc_ids: list[int]) -> dict[int, dict]:
    """Per-document table count, needs_review row count and last table-job status."""
    stats: dict[int, dict] = {
        d: {"table_count": 0, "needs_review_rows": 0, "last_table_job_status": None}
        for d in doc_ids
    }
    if not doc_ids:
        return stats
    for doc_id, count in (
        db.query(Page.document_id, func.count(MaterialTable.id))
        .join(MaterialTable, MaterialTable.page_id == Page.id)
        .filter(Page.document_id.in_(doc_ids))
        .group_by(Page.document_id)
    ):
        stats[doc_id]["table_count"] = count
    for doc_id, count in (
        db.query(Page.document_id, func.count(MaterialRow.id))
        .join(MaterialTable, MaterialTable.page_id == Page.id)
        .join(MaterialRow, MaterialRow.table_id == MaterialTable.id)
        .filter(Page.document_id.in_(doc_ids), MaterialRow.status == "needs_review")
        .group_by(Page.document_id)
    ):
        stats[doc_id]["needs_review_rows"] = count
    for job in (
        db.query(ExtractionJob)
        .filter(ExtractionJob.document_id.in_(doc_ids), ExtractionJob.kind == "tables")
        .order_by(ExtractionJob.id)
    ):
        stats[job.document_id]["last_table_job_status"] = job.status
    return stats


@router.post("/projects", response_model=ProjectOut, status_code=201)
def create_project(body: ProjectIn, db: Session = Depends(get_db)):
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "Project name must not be empty")
    project = Project(name=name, note=body.note)
    db.add(project)
    tracker.emit(db, "project_created", entity_id=None)
    db.commit()
    db.refresh(project)
    return project


@router.get("/projects", response_model=list[ProjectListOut])
def list_projects(db: Session = Depends(get_db)):
    projects = db.query(Project).order_by(Project.created_at.desc()).all()
    out = []
    for p in projects:
        doc_ids = [d.id for d in p.documents]
        stats = _doc_table_stats(db, doc_ids)
        out.append(
            ProjectListOut(
                id=p.id,
                name=p.name,
                note=p.note,
                created_at=p.created_at,
                document_count=len(doc_ids),
                table_count=sum(s["table_count"] for s in stats.values()),
                needs_review_rows=sum(s["needs_review_rows"] for s in stats.values()),
            )
        )
    return out


@router.get("/projects/{project_id}", response_model=ProjectDetailOut)
def get_project(project_id: int, db: Session = Depends(get_db)):
    project = _get_project(db, project_id)
    doc_ids = [d.id for d in project.documents]
    stats = _doc_table_stats(db, doc_ids)
    return ProjectDetailOut(
        id=project.id,
        name=project.name,
        note=project.note,
        created_at=project.created_at,
        documents=[
            ProjectDocumentOut(
                **{
                    "id": d.id,
                    "filename": d.filename,
                    "sha256": d.sha256,
                    "page_count": d.page_count,
                    "status": d.status,
                    "created_at": d.created_at,
                },
                **stats[d.id],
            )
            for d in project.documents
        ],
    )


@router.patch("/projects/{project_id}", response_model=ProjectOut)
def patch_project(project_id: int, body: ProjectPatchIn, db: Session = Depends(get_db)):
    project = _get_project(db, project_id)
    if body.name is not None:
        name = body.name.strip()
        if not name:
            raise HTTPException(422, "Project name must not be empty")
        project.name = name
    if body.note is not None:
        project.note = body.note
    db.commit()
    db.refresh(project)
    return project


@router.delete("/projects/{project_id}", status_code=204)
def delete_project(project_id: int, db: Session = Depends(get_db)):
    project = _get_project(db, project_id)
    doc_ids = [d.id for d in project.documents]
    if doc_ids:
        approved = (
            db.query(MaterialTable.id)
            .join(Page, MaterialTable.page_id == Page.id)
            .filter(Page.document_id.in_(doc_ids), MaterialTable.status == "approved")
            .first()
        )
        if approved:
            raise HTTPException(
                409, "Project has approved tables; detach or reject them first"
            )
    # documents survive the project — they stay visible in the Documents view
    for doc in project.documents:
        doc.project_id = None
    # prices and order plans belong to the project — they go with it
    db.query(MaterialPrice).filter(
        MaterialPrice.project_id == project_id
    ).delete(synchronize_session=False)
    db.query(OrderPlan).filter(
        OrderPlan.project_id == project_id
    ).delete(synchronize_session=False)
    db.delete(project)
    db.commit()


@router.post(
    "/projects/{project_id}/documents",
    response_model=DocumentDetailOut,
    status_code=201,
)
async def upload_project_document(
    project_id: int, file: UploadFile, db: Session = Depends(get_db)
):
    project = _get_project(db, project_id)
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, "Only PDF, JPEG, or PNG files are supported")
    content = await file.read()
    try:
        doc = ingest_document(db, file.filename, content, suffix)
    except DuplicateDocumentError as e:
        existing = db.get(Document, e.existing_id)
        if existing and existing.project_id is None:
            # adopt an orphan duplicate instead of failing the drop
            existing.project_id = project.id
            db.commit()
            db.refresh(existing)
            return existing
        raise HTTPException(
            409, f"Document already ingested (id={e.existing_id})"
        ) from e
    doc.project_id = project.id
    tracker.emit(db, "project_document_added", entity_id=doc.id)
    db.commit()
    db.refresh(doc)
    if settings.table_autorun_on_upload:
        job = create_table_job(db, doc)
        worker.enqueue(job.id)
    return doc


@router.post(
    "/projects/{project_id}/table-jobs", response_model=list[JobOut], status_code=202
)
def create_project_table_jobs(
    project_id: int, only_failed: bool = False, db: Session = Depends(get_db)
):
    """(Re)scan documents that aren't already queued/running.

    only_failed=true rescans just the documents whose LAST scan failed — the
    queue panel's "retry failed" button, which must not burn time re-reading
    documents that already scanned clean."""
    project = _get_project(db, project_id)
    jobs = []
    for doc in project.documents:
        last = (
            db.query(ExtractionJob)
            .filter(
                ExtractionJob.document_id == doc.id, ExtractionJob.kind == "tables"
            )
            .order_by(ExtractionJob.id.desc())
            .first()
        )
        if last and last.status in ("queued", "running"):
            continue
        if only_failed and (last is None or last.status != "failed"):
            continue
        job = create_table_job(db, doc)
        worker.enqueue(job.id)
        jobs.append(JobOut.model_validate(job))
    return jobs


@router.get("/projects/{project_id}/queue")
def project_queue(project_id: int, db: Session = Depends(get_db)):
    """The project's scan queue, as the operator should see it: what is being
    scanned now, what is waiting (and where it stands in the global line),
    what finished, what failed — plus an ETA from this project's own history."""
    project = _get_project(db, project_id)
    doc_by_id = {d.id: d for d in project.documents}

    # latest table job per document decides that document's state
    latest: dict[int, ExtractionJob] = {}
    if doc_by_id:
        for job in (
            db.query(ExtractionJob)
            .filter(
                ExtractionJob.document_id.in_(doc_by_id),
                ExtractionJob.kind == "tables",
            )
            .order_by(ExtractionJob.id)
        ):
            latest[job.document_id] = job

    running = [j for j in latest.values() if j.status == "running"]
    queued = sorted(
        (j for j in latest.values() if j.status == "queued"), key=lambda j: j.id
    )
    done = [j for j in latest.values() if j.status == "done"]
    failed = [j for j in latest.values() if j.status == "failed"]
    unscanned = [d for d in project.documents if d.id not in latest]

    # position in the GLOBAL line (other projects' jobs run ahead too)
    global_queued_ids = [
        job_id
        for (job_id,) in db.query(ExtractionJob.id)
        .filter(ExtractionJob.status == "queued")
        .order_by(ExtractionJob.id)
    ]
    global_pos = {job_id: i + 1 for i, job_id in enumerate(global_queued_ids)}

    # ETA from this project's own completed scans (fallback: any project's)
    durations = [
        (j.finished_at - j.started_at).total_seconds()
        for j in done
        if j.finished_at and j.started_at
    ]
    if not durations:
        recent = (
            db.query(ExtractionJob)
            .filter(
                ExtractionJob.kind == "tables",
                ExtractionJob.status == "done",
                ExtractionJob.finished_at.isnot(None),
            )
            .order_by(ExtractionJob.id.desc())
            .limit(10)
            .all()
        )
        durations = [
            (j.finished_at - j.started_at).total_seconds()
            for j in recent
            if j.finished_at and j.started_at
        ]
    avg = sum(durations) / len(durations) if durations else None
    remaining = len(queued) + len(running)
    eta = round(avg * remaining) if avg and remaining else None

    def _entry(job: ExtractionJob) -> dict:
        doc = doc_by_id[job.document_id]
        return {
            "job_id": job.id,
            "document_id": doc.id,
            "filename": doc.filename,
            "status": job.status,
            "queue_position": global_pos.get(job.id),
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "error": job.error,
        }

    return {
        "total_documents": len(project.documents),
        "scanned": len(done),
        "running": [_entry(j) for j in running],
        "queued": [_entry(j) for j in queued],
        "failed": [_entry(j) for j in failed],
        "unscanned": [
            {"document_id": d.id, "filename": d.filename} for d in unscanned
        ],
        "avg_scan_seconds": round(avg, 1) if avg else None,
        "eta_seconds": eta,
    }
