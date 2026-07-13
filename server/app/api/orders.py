import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.models import OrderPlan, Project
from app.db.session import get_db
from app.orders.optimizer import optimize, plan_to_dict
from app.tables.aggregate import project_summary

router = APIRouter(prefix="/api", tags=["orders"])


class PieceIn(BaseModel):
    length_mm: float = Field(gt=0)
    qty: int = Field(gt=0)


class StockIn(BaseModel):
    length_mm: float = Field(gt=0)
    price: float = Field(ge=0)


class OrderPlanIn(BaseModel):
    stock: list[StockIn]
    kerf_mm: float = Field(default=0.0, ge=0)
    # explicit pieces, or default to a material line from the project summary
    pieces: list[PieceIn] | None = None
    material_key: str | None = None


def _project_or_404(db: Session, project_id: int) -> Project:
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    return project


@router.post("/projects/{project_id}/order-plans", status_code=201)
def create_order_plan(
    project_id: int, body: OrderPlanIn, db: Session = Depends(get_db)
):
    _project_or_404(db, project_id)
    if not body.stock:
        raise HTTPException(422, "at least one stock length required")

    if body.pieces:
        pieces = [(p.length_mm, p.qty) for p in body.pieces]
    elif body.material_key:
        summary = project_summary(db, [project_id])
        row = next(
            (r for r in summary["rows"] if r["material_key"] == body.material_key),
            None,
        )
        if row is None:
            raise HTTPException(
                404, f"{body.material_key} not in the approved project summary"
            )
        pieces = [(l["unit_length_mm"], l["qty"]) for l in row["lengths"]]
        if not pieces:
            raise HTTPException(
                422, f"{body.material_key} has no per-length breakdown to order from"
            )
    else:
        raise HTTPException(422, "provide pieces or a material_key")

    plan = optimize(pieces, [(s.length_mm, s.price) for s in body.stock], body.kerf_mm)
    result = plan_to_dict(plan, body.kerf_mm)

    row = OrderPlan(
        project_id=project_id,
        params_json=json.dumps(
            {
                "material_key": body.material_key,
                "pieces": [{"length_mm": p[0], "qty": p[1]} for p in pieces],
                "stock": [s.model_dump() for s in body.stock],
                "kerf_mm": body.kerf_mm,
            }
        ),
        result_json=json.dumps(result),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _plan_out(row)


def _plan_out(row: OrderPlan) -> dict:
    return {
        "id": row.id,
        "project_id": row.project_id,
        "created_at": row.created_at.isoformat()
        if isinstance(row.created_at, datetime)
        else row.created_at,
        "params": json.loads(row.params_json),
        "result": json.loads(row.result_json),
    }


@router.get("/projects/{project_id}/order-plans")
def list_order_plans(project_id: int, db: Session = Depends(get_db)):
    _project_or_404(db, project_id)
    rows = (
        db.query(OrderPlan)
        .filter(OrderPlan.project_id == project_id)
        .order_by(OrderPlan.id.desc())
        .all()
    )
    return [_plan_out(r) for r in rows]


@router.get("/order-plans/{plan_id}")
def get_order_plan(plan_id: int, db: Session = Depends(get_db)):
    row = db.get(OrderPlan, plan_id)
    if not row:
        raise HTTPException(404, "Order plan not found")
    return _plan_out(row)
