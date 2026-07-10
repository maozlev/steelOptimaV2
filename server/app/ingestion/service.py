import hashlib
import json

import fitz
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import Document, Page
from app.ingestion.page_classifier import classify_page
from app.ingestion.renderer import render_page

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}


class DuplicateDocumentError(Exception):
    def __init__(self, existing_id: int):
        self.existing_id = existing_id


class RotatedPageError(Exception):
    pass


def ingest_document(
    db: Session, filename: str, content: bytes, suffix: str = ".pdf"
) -> Document:
    # dedupe on the raw uploaded bytes, before any image->PDF conversion
    sha256 = hashlib.sha256(content).hexdigest()

    existing = db.query(Document).filter_by(sha256=sha256).first()
    if existing:
        raise DuplicateDocumentError(existing.id)

    if suffix in IMAGE_SUFFIXES:
        with fitz.open(stream=content, filetype=suffix.lstrip(".")) as img:
            content = img.convert_to_pdf()

    original_path = settings.originals_dir / f"{sha256}.pdf"
    original_path.write_bytes(content)

    with fitz.open(stream=content, filetype="pdf") as pdf:
        doc = Document(
            filename=filename,
            sha256=sha256,
            path=str(original_path),
            page_count=len(pdf),
        )
        db.add(doc)
        db.flush()

        for i, page in enumerate(pdf):
            render_path = settings.renders_dir / str(doc.id) / f"page_{i}.png"
            effective_dpi = render_page(page, render_path, settings.render_dpi)
            db.add(
                Page(
                    document_id=doc.id,
                    index=i,
                    kind=classify_page(page),
                    width_pt=page.rect.width,
                    height_pt=page.rect.height,
                    render_path=str(render_path),
                    render_dpi=effective_dpi,
                )
            )

    db.commit()
    db.refresh(doc)
    return doc


def apply_crop(
    db: Session, doc: Document, crop: tuple[float, float, float, float]
) -> Document:
    x_min, y_min, x_max, y_max = crop

    with fitz.open(doc.path) as pdf:
        for page in pdf:
            r = page.rect  # rotated/display space — matches what the client previews
            box = fitz.Rect(
                r.x0 + x_min * r.width,
                r.y0 + y_min * r.height,
                r.x0 + x_max * r.width,
                r.y0 + y_max * r.height,
            )
            # set_cropbox expects unrotated coordinates
            box = box * page.derotation_matrix
            box.normalize()
            try:
                page.set_cropbox(box & page.cropbox)
            except ValueError as e:
                raise RotatedPageError() from e
        cropped_bytes = pdf.tobytes()

    cropped_path = settings.originals_dir / f"{doc.sha256}_cropped.pdf"
    cropped_path.write_bytes(cropped_bytes)
    doc.path = str(cropped_path)
    doc.crop_json = json.dumps(
        {"x_min": x_min, "y_min": y_min, "x_max": x_max, "y_max": y_max}
    )

    pages = {p.index: p for p in db.query(Page).filter_by(document_id=doc.id).all()}
    with fitz.open(str(cropped_path)) as pdf:
        for i, page in enumerate(pdf):
            row = pages.get(i)
            if row is None:
                continue
            effective_dpi = render_page(
                page, settings.renders_dir / str(doc.id) / f"page_{i}.png", settings.render_dpi
            )
            row.width_pt = page.rect.width
            row.height_pt = page.rect.height
            row.render_dpi = effective_dpi

    db.flush()
    return doc
