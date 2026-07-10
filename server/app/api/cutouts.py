import json

from fastapi import APIRouter, Depends, HTTPException
from shapely import wkt as shapely_wkt
from sqlalchemy.orm import Session

from app.api.documents import ensure_unlocked
from app.db.models import Cutout, Page
from app.db.session import get_db
from app.schemas.cutouts import CutoutCreateIn, CutoutPatchIn
from app.schemas.jobs import CutoutOut
from app.telemetry import tracker

router = APIRouter(prefix="/api", tags=["cutouts"])


def _parse_polygon_wkt(text: str):
    try:
        geom = shapely_wkt.loads(text)
    except Exception:
        raise HTTPException(422, "Invalid WKT")
    if geom.geom_type != "Polygon" or not geom.is_valid or geom.is_empty:
        raise HTTPException(422, "Geometry must be a valid non-empty Polygon")
    return geom


@router.patch("/cutouts/{cutout_id}", response_model=CutoutOut)
def patch_cutout(
    cutout_id: int, body: CutoutPatchIn, db: Session = Depends(get_db)
):
    cutout = db.get(Cutout, cutout_id)
    if not cutout:
        raise HTTPException(404, "Cutout not found")
    page = db.get(Page, cutout.page_id)
    ensure_unlocked(page.document)

    if body.action == "approve":
        cutout.status = "approved"
    elif body.action == "reject":
        cutout.status = "rejected"
    else:
        if body.geometry_wkt is None and body.kind is None:
            raise HTTPException(422, "edit requires geometry_wkt and/or kind")
        if body.geometry_wkt is not None:
            geom = _parse_polygon_wkt(body.geometry_wkt)
            # geometry_wkt keeps the original detection for audit
            cutout.edited_geometry_wkt = geom.wkt
            cutout.bbox = json.dumps(list(geom.bounds))
        if body.kind is not None:
            cutout.kind = body.kind
        cutout.status = "edited"

    tracker.emit(
        db,
        f"cutout_{cutout.status}",
        entity_id=cutout.id,
        payload={"action": body.action, "source": cutout.source},
        session_id=body.session_id,
    )
    db.commit()
    db.refresh(cutout)
    return cutout


@router.post(
    "/pages/{page_id}/cutouts", response_model=CutoutOut, status_code=201
)
def add_cutout(page_id: int, body: CutoutCreateIn, db: Session = Depends(get_db)):
    page = db.get(Page, page_id)
    if not page:
        raise HTTPException(404, "Page not found")
    ensure_unlocked(page.document)
    geom = _parse_polygon_wkt(body.geometry_wkt)
    cutout = Cutout(
        page_id=page_id,
        job_id=None,
        geometry_wkt=geom.wkt,
        bbox=json.dumps(list(geom.bounds)),
        kind=body.kind,
        source="manual",
        confidence=1.0,
        dimension_text=body.dimension_text,
        status="approved",
    )
    db.add(cutout)
    db.flush()
    tracker.emit(
        db,
        "cutout_added",
        entity_id=cutout.id,
        payload={"kind": body.kind},
        session_id=body.session_id,
    )
    db.commit()
    db.refresh(cutout)
    return cutout
