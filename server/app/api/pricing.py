from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.models import MaterialPrice, Project
from app.db.session import get_db
from app.tables.pricing import PRICING_UNITS, compute_bid, upsert_prices

router = APIRouter(prefix="/api", tags=["pricing"])


class PriceEntryIn(BaseModel):
    material_key: str
    price: float = Field(ge=0)
    pricing_unit: str

    def validated(self) -> dict:
        if self.pricing_unit not in PRICING_UNITS:
            raise HTTPException(422, f"pricing_unit must be one of {PRICING_UNITS}")
        return self.model_dump()


class PricesIn(BaseModel):
    entries: list[PriceEntryIn]


def _project_or_404(db: Session, project_id: int) -> Project:
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    return project


@router.get("/projects/{project_id}/prices")
def get_prices(project_id: int, db: Session = Depends(get_db)):
    _project_or_404(db, project_id)
    entries = (
        db.query(MaterialPrice)
        .filter(MaterialPrice.project_id == project_id)
        .order_by(MaterialPrice.material_key)
        .all()
    )
    return {
        "entries": [
            {
                "material_key": p.material_key,
                "price": p.price,
                "pricing_unit": p.pricing_unit,
            }
            for p in entries
        ]
    }


@router.put("/projects/{project_id}/prices")
def put_prices(project_id: int, body: PricesIn, db: Session = Depends(get_db)):
    _project_or_404(db, project_id)
    written = upsert_prices(db, project_id, [e.validated() for e in body.entries])
    return {"written": written}


@router.get("/projects/{project_id}/bid")
def get_bid(project_id: int, ids: str | None = None, db: Session = Depends(get_db)):
    """Bid for this project, or a merged bid with ?ids=2,3 added."""
    _project_or_404(db, project_id)
    project_ids = [project_id]
    if ids:
        try:
            project_ids += [
                int(x) for x in ids.split(",") if x.strip() and int(x) != project_id
            ]
        except ValueError:
            raise HTTPException(422, "ids must be comma-separated integers")
    return compute_bid(db, project_ids)
