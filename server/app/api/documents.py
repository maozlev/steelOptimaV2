from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import (
    Cutout,
    Document,
    ExtractionJob,
    MaterialRow,
    MaterialTable,
    Page,
    TelemetryEvent,
    VlmCall,
)
from app.db.session import get_db
from app.ingestion.overlay import render_overlay
from app.ingestion.service import (
    DuplicateDocumentError,
    RotatedPageError,
    apply_crop,
    ingest_document,
)
from app.schemas.documents import (
    DocumentCropIn,
    DocumentDetailOut,
    DocumentOut,
    FinalizeIn,
    FinalizeOut,
    PageOut,
)
from app.schemas.jobs import CutoutOut
from app.telemetry import tracker

router = APIRouter(prefix="/api", tags=["documents"])

ALLOWED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png"}


def ensure_unlocked(doc: Document) -> None:
    if doc.status == "approved":
        raise HTTPException(409, "Document is approved and locked")


@router.get("/config")
def get_config():
    return {
        "escalation_threshold": settings.escalation_threshold,
        "finalize_threshold": settings.finalize_threshold,
    }


@router.post("/documents", response_model=DocumentDetailOut, status_code=201)
async def upload_document(file: UploadFile, db: Session = Depends(get_db)):
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, "Only PDF, JPEG, or PNG files are supported")
    content = await file.read()
    try:
        return ingest_document(db, file.filename, content, suffix)
    except DuplicateDocumentError as e:
        raise HTTPException(
            409, f"Document already ingested (id={e.existing_id})"
        ) from e


@router.post("/documents/{doc_id}/crop", response_model=DocumentDetailOut)
def crop_document(doc_id: int, body: DocumentCropIn, db: Session = Depends(get_db)):
    doc = db.get(Document, doc_id)
    if not doc:
        raise HTTPException(404, "Document not found")
    ensure_unlocked(doc)
    if db.query(ExtractionJob.id).filter_by(document_id=doc_id).first():
        raise HTTPException(409, "Document already has extraction jobs; crop is pre-extraction only")
    has_cutouts = (
        db.query(Cutout.id)
        .join(Page, Cutout.page_id == Page.id)
        .filter(Page.document_id == doc_id)
        .first()
    )
    if has_cutouts:
        raise HTTPException(409, "Document already has cutouts; crop is pre-extraction only")
    if not (
        0.0 <= body.x_min < body.x_max <= 1.0 and 0.0 <= body.y_min < body.y_max <= 1.0
    ):
        raise HTTPException(422, "Crop coordinates must satisfy 0 <= min < max <= 1")

    area = (body.x_max - body.x_min) * (body.y_max - body.y_min)
    if area >= 0.995:
        return doc

    try:
        apply_crop(db, doc, (body.x_min, body.y_min, body.x_max, body.y_max))
    except RotatedPageError:
        raise HTTPException(422, "Crop region is invalid for this page")
    tracker.emit(
        db,
        "document_cropped",
        entity_id=doc.id,
        payload={
            "x_min": body.x_min,
            "y_min": body.y_min,
            "x_max": body.x_max,
            "y_max": body.y_max,
        },
    )
    db.commit()
    db.refresh(doc)
    return doc


@router.post("/documents/{doc_id}/finalize", response_model=FinalizeOut)
def finalize_document(
    doc_id: int, body: FinalizeIn | None = None, db: Session = Depends(get_db)
):
    doc = db.get(Document, doc_id)
    if not doc:
        raise HTTPException(404, "Document not found")
    if doc.status == "approved":
        raise HTTPException(409, "Document already finalized")

    # The scale is the operator's call, and nothing gets cut from a size nobody signed off
    # on. Every dimension in the BOM is a paper measurement multiplied by this number; with
    # it unset or unconfirmed, the export is the size of ink on a page, not of a part.
    unconfirmed = [
        p.index
        for p in db.query(Page).filter(Page.document_id == doc_id).order_by(Page.index)
        if p.scale is None or not p.scale_confirmed
    ]
    if unconfirmed:
        pages = ", ".join(f"page {i + 1}" for i in unconfirmed)
        raise HTTPException(
            409,
            f"Scale not confirmed for {pages}. Every dimension here is a paper "
            f"measurement multiplied by the sheet scale — set and confirm it before "
            f"finalizing, or the parts will be cut at the wrong size.",
        )

    cutouts = (
        db.query(Cutout)
        .join(Page, Cutout.page_id == Page.id)
        .filter(Page.document_id == doc_id)
        .all()
    )
    auto_approved = auto_rejected = already_reviewed = 0
    for c in cutouts:
        if c.status != "pending":
            already_reviewed += 1
        elif c.confidence >= settings.finalize_threshold:
            c.status = "approved"
            auto_approved += 1
        else:
            c.status = "rejected"
            auto_rejected += 1

    doc.status = "approved"
    tracker.emit(
        db,
        "document_finalized",
        entity_id=doc.id,
        payload={
            "auto_approved": auto_approved,
            "auto_rejected": auto_rejected,
            "already_reviewed": already_reviewed,
        },
        session_id=body.session_id if body else None,
    )
    db.commit()
    db.refresh(doc)
    return FinalizeOut(
        document=DocumentOut.model_validate(doc),
        auto_approved=auto_approved,
        auto_rejected=auto_rejected,
        already_reviewed=already_reviewed,
    )


@router.get("/documents", response_model=list[DocumentOut])
def list_documents(db: Session = Depends(get_db)):
    return db.query(Document).order_by(Document.created_at.desc()).all()


@router.get("/documents/{doc_id}", response_model=DocumentDetailOut)
def get_document(doc_id: int, db: Session = Depends(get_db)):
    doc = db.get(Document, doc_id)
    if not doc:
        raise HTTPException(404, "Document not found")
    return doc


@router.get("/documents/{doc_id}/cutouts", response_model=list[CutoutOut])
def list_document_cutouts(doc_id: int, db: Session = Depends(get_db)):
    doc = db.get(Document, doc_id)
    if not doc:
        raise HTTPException(404, "Document not found")
    page_ids = [p.id for p in doc.pages]
    if not page_ids:
        return []
    return (
        db.query(Cutout)
        .filter(Cutout.page_id.in_(page_ids))
        .order_by(Cutout.confidence.desc())
        .all()
    )


@router.delete("/documents/{doc_id}", status_code=204)
def delete_document(doc_id: int, db: Session = Depends(get_db)):
    doc = db.get(Document, doc_id)
    if not doc:
        raise HTTPException(404, "Document not found")

    pages = db.query(Page).filter_by(document_id=doc_id).all()
    page_ids = [p.id for p in pages]
    render_paths = [Path(p.render_path) for p in pages]
    original_path = Path(doc.path)

    # delete child rows in FK order before cascade can handle them.
    # dependency chain: material_rows -> material_tables -> {pages, jobs};
    # vlm_calls -> {jobs, cutouts, material_tables}; cutouts -> pages.
    jobs = db.query(ExtractionJob).filter_by(document_id=doc_id).all()
    job_ids = [j.id for j in jobs]
    tables = (
        db.query(MaterialTable).filter(MaterialTable.page_id.in_(page_ids)).all()
        if page_ids
        else []
    )
    table_ids = [t.id for t in tables]
    table_crop_paths = [Path(t.crop_path) for t in tables if t.crop_path]

    if table_ids:
        db.query(MaterialRow).filter(
            MaterialRow.table_id.in_(table_ids)
        ).delete(synchronize_session=False)
    if job_ids:
        db.query(VlmCall).filter(VlmCall.job_id.in_(job_ids)).delete(synchronize_session=False)
    if table_ids:
        db.query(MaterialTable).filter(
            MaterialTable.id.in_(table_ids)
        ).delete(synchronize_session=False)
    if page_ids:
        db.query(Cutout).filter(Cutout.page_id.in_(page_ids)).delete(synchronize_session=False)
    for job in jobs:
        db.delete(job)
    db.delete(doc)  # cascades to Page rows
    db.commit()

    for p in render_paths:
        p.unlink(missing_ok=True)
    for p in table_crop_paths:
        p.unlink(missing_ok=True)
    original_path.unlink(missing_ok=True)


@router.get("/documents/{doc_id}/pages", response_model=list[PageOut])
def list_pages(doc_id: int, db: Session = Depends(get_db)):
    if not db.get(Document, doc_id):
        raise HTTPException(404, "Document not found")
    return db.query(Page).filter_by(document_id=doc_id).order_by(Page.index).all()


@router.get("/pages/{page_id}/render")
def get_page_render(
    page_id: int,
    overlay: bool = False,
    min_conf: float = 0.0,
    db: Session = Depends(get_db),
):
    page = db.get(Page, page_id)
    if not page:
        raise HTTPException(404, "Page not found")
    render = Path(page.render_path)
    if not render.exists():
        raise HTTPException(500, "Render file missing")
    # page ids are reused across deletes/crops re-render in place — a cached
    # image desyncs from fresh cutout JSON, so renders must never be cached
    no_cache = {"Cache-Control": "no-store"}
    if not overlay:
        return FileResponse(render, media_type="image/png", headers=no_cache)

    cutouts = (
        db.query(Cutout)
        .filter(Cutout.page_id == page_id, Cutout.confidence >= min_conf)
        .all()
    )
    png = render_overlay(render, page.render_dpi, cutouts)
    return Response(content=png, media_type="image/png", headers=no_cache)
