import json

from fastapi import APIRouter, Depends, HTTPException
from shapely import wkt as shapely_wkt
from sqlalchemy.orm import Session

from app.bom.service import build_rows, cutout_metrics, page_scales, totals
from app.db.models import Cutout, Document, Page
from app.db.session import get_db
from app.extraction.vector import PT_TO_MM
from app.telemetry import tracker

router = APIRouter(prefix="/api", tags=["export"])

EXPORT_STATUSES = ("approved", "edited")


def _cutout_payload(c: Cutout, scale: float | None) -> dict:
    wkt_text = c.edited_geometry_wkt or c.geometry_wkt
    geom = shapely_wkt.loads(wkt_text)
    # points are the drawing's own coordinates, scaled to reality
    s = (scale or 1.0) * PT_TO_MM
    points_mm = [[round(x * s, 3), round(y * s, 3)] for x, y in geom.exterior.coords]
    metrics = cutout_metrics(c, scale)
    return {
        "id": c.id,
        "kind": c.kind,
        "shape": metrics["shape"],
        "cut_length_mm": metrics["cut_length_mm"],
        "source": c.source,
        "status": c.status,
        "confidence": c.confidence,
        "geometry_wkt_pt": wkt_text,
        "points_mm": points_mm,
        "centroid_mm": [
            round(geom.centroid.x * s, 3),
            round(geom.centroid.y * s, 3),
        ],
        "dims": json.loads(c.measured_dims_json) if c.measured_dims_json else None,
        "dimension_text": c.dimension_text,
    }


@router.get("/documents/{doc_id}/export")
def export_document(doc_id: int, db: Session = Depends(get_db)):
    doc = db.get(Document, doc_id)
    if not doc:
        raise HTTPException(404, "Document not found")

    pages = []
    exported: list[Cutout] = []
    unverified = []
    for page in doc.pages:
        cutouts = (
            db.query(Cutout)
            .filter(Cutout.page_id == page.id, Cutout.status.in_(EXPORT_STATUSES))
            .order_by(Cutout.id)
            .all()
        )
        exported += cutouts
        if page.scale is None or not page.scale_confident:
            unverified.append(page.index)
        pages.append(
            {
                "index": page.index,
                "kind": page.kind,
                "scale": page.scale,
                "scale_confident": page.scale_confident,
                "width_mm": round(page.width_pt * (page.scale or 1.0) * PT_TO_MM, 3),
                "height_mm": round(page.height_pt * (page.scale or 1.0) * PT_TO_MM, 3),
                "cutouts": [_cutout_payload(c, page.scale) for c in cutouts],
            }
        )

    rows = build_rows(exported, page_scales(db, exported))
    total = len(exported)
    tracker.emit(db, "document_exported", entity_id=doc_id, payload={"cutouts": total})
    db.commit()
    return {
        "document": {
            "id": doc.id,
            "filename": doc.filename,
            "sha256": doc.sha256,
            "page_count": doc.page_count,
        },
        "units": "mm",
        # real-world millimetres: paper measurements multiplied by each page's sheet
        # scale. Pages listed in scale_unverified had no scale the drawing's own
        # dimensions could confirm — their sizes are NOT safe to cut from.
        "scale_unverified_pages": unverified,
        # y axis follows PDF page coords (origin top-left, y down); DXF
        # consumers typically flip y against the page height
        "coordinate_system": "page_top_left_y_down",
        "cutout_count": total,
        "bom": {"rows": rows, "totals": totals(rows)},
        "pages": pages,
    }
