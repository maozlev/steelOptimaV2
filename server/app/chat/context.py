"""The data a chat is allowed to know, per scope, as compact JSON.

Three scopes, three builders, one rule: the context is rebuilt from the DB on
every question, so the model always answers from the operator's current numbers
— never a snapshot from when the conversation started. Everything here reuses
the same aggregation code the UI reads (bom.service, tables.aggregate,
tables.pricing), so the chat can never disagree with the screen.

The context must FIT: chat_num_ctx tokens hold the system prompt, this JSON,
the history and the answer. Builders slim rows to the fields worth asking
about, and _fit() degrades gracefully (drop table cell rows first, then halve
row lists) rather than silently overflowing the model's window — an overflowed
context makes Ollama truncate from the TOP, which silently deletes the rules.
"""

import json

from sqlalchemy.orm import Session

from app.api.bom import _document_cutouts, _scale_status
from app.bom.service import build_rows, page_scales, totals
from app.config import settings
from app.db.models import Document, MaterialTable, OrderPlan, Page, Project
from app.tables.aggregate import project_summary
from app.tables.pricing import compute_bid

MAX_ORDER_PLANS = 5


def _dumps(obj: dict) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _fit(context: dict) -> str:
    """Serialize under the char cap, degrading the bulkiest parts first."""
    text = _dumps(context)
    if len(text) <= settings.chat_context_max_chars:
        return text

    # 1st: table cell rows are the bulk — keep each table's metadata + totals
    for table in context.get("material_tables", []):
        if "rows" in table:
            table["rows_omitted_for_size"] = len(table.pop("rows"))
    text = _dumps(context)

    # 2nd: halve the remaining row lists until it fits
    while len(text) > settings.chat_context_max_chars:
        shrunk = False
        for key in (
            "cutout_bom",
            "materials_with_pricing",
            "materials_all_projects",
            "material_tables",
            "order_plans",
            "documents",
            "projects",
        ):
            value = context.get(key)
            rows = value.get("rows") if isinstance(value, dict) else value
            if isinstance(rows, list) and len(rows) > 4:
                kept = len(rows) // 2
                del rows[kept:]
                (value if isinstance(value, dict) else context)[
                    "truncated_for_size"
                ] = True
                shrunk = True
        if not shrunk:
            break
        text = _dumps(context)
    return text


def _slim_bom_row(r: dict) -> dict:
    return {
        "shape": r["shape_label"],
        "size": r["dims"],
        "qty": r["qty"],
        "cut_length_each_mm": r["cut_length_each_mm"],
        "cut_length_total_mm": r["cut_length_total_mm"],
        "needs_review": r["needs_review"],
    }


def _slim_summary_row(r: dict) -> dict:
    out = {
        "material_key": r["material_key"],
        "description": r.get("description"),
        "qty": r["qty"],
        "total_length_mm": r["total_length_mm"],
        "total_weight_kg": r["total_weight_kg"],
        "lengths": r["lengths"],
        "documents": r["documents"],
    }
    # bid rows are summary rows plus pricing
    if "price" in r:
        out["price"] = r["price"]
        out["pricing_unit"] = r["pricing_unit"]
        out["line_total"] = r["line_total"]
    if len(r.get("projects", [])) > 1:
        out["projects"] = r["projects"]
    return out


def _document_tables(db: Session, doc_id: int) -> list[dict]:
    tables = (
        db.query(MaterialTable, Page)
        .join(Page, MaterialTable.page_id == Page.id)
        .filter(Page.document_id == doc_id)
        .order_by(MaterialTable.id)
        .all()
    )
    out = []
    for t, page in tables:
        out.append(
            {
                "table_id": t.id,
                "page_index": page.index,
                "kind": t.kind,
                "title": t.title,
                "status": t.status,
                "n_rows": t.n_rows,
                "declared_total_weight_kg": t.declared_total_weight_kg,
                "validation": json.loads(t.validation_json or "null"),
                "rows": [
                    {
                        "row": r.row_index,
                        "material_key": r.material_key,
                        "description": r.description,
                        "qty": r.qty,
                        "unit_length_mm": r.unit_length_mm,
                        "total_length_mm": r.total_length_mm,
                        "unit_weight_kg": r.unit_weight_kg,
                        "total_weight_kg": r.total_weight_kg,
                        "status": r.status,
                        "flags": json.loads(r.flags_json or "[]"),
                    }
                    for r in t.rows
                ],
            }
        )
    return out


def _slim_plan(p: OrderPlan) -> dict:
    params = json.loads(p.params_json)
    result = json.loads(p.result_json)
    return {
        "plan_id": p.id,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "material_key": params.get("material_key"),
        "kerf_mm": params.get("kerf_mm"),
        "order": result.get("order"),
        "total_cost": result.get("total_cost"),
        "total_bought_mm": result.get("total_bought_mm"),
        "waste_pct": result.get("waste_pct"),
        "infeasible_lengths_mm": result.get("infeasible_lengths_mm"),
    }


def _project_plans(db: Session, project_id: int) -> list[dict]:
    plans = (
        db.query(OrderPlan)
        .filter(OrderPlan.project_id == project_id)
        .order_by(OrderPlan.id.desc())
        .limit(MAX_ORDER_PLANS)
        .all()
    )
    return [_slim_plan(p) for p in plans]


def document_context(db: Session, doc_id: int) -> str | None:
    doc = db.get(Document, doc_id)
    if doc is None:
        return None
    cutouts = _document_cutouts(db, doc_id)
    bom_rows = build_rows(cutouts, page_scales(db, cutouts))
    return _fit(
        {
            "scope": "one engineering drawing document",
            "document": {
                "filename": doc.filename,
                "status": doc.status,
                "pages": doc.page_count,
                "project": doc.project.name if doc.project else None,
            },
            "sheet_scale": _scale_status(db, doc_id),
            "cutout_bom": {
                "rows": [_slim_bom_row(r) for r in bom_rows],
                "totals": totals(bom_rows),
            },
            "material_tables": _document_tables(db, doc_id),
        }
    )


def project_context(db: Session, project_id: int) -> str | None:
    project = db.get(Project, project_id)
    if project is None:
        return None
    bid = compute_bid(db, [project_id])
    return _fit(
        {
            "scope": "one project",
            "project": {"name": project.name, "note": project.note},
            "documents": [
                {"filename": d.filename, "status": d.status, "pages": d.page_count}
                for d in project.documents
            ],
            "materials_with_pricing": {
                "rows": [_slim_summary_row(r) for r in bid["rows"]],
                "bid_total": bid["total"],
                "unpriced_material_keys": bid["unpriced_keys"],
            },
            "not_included_in_numbers": bid["unreviewed"],
            "order_plans": _project_plans(db, project_id),
        }
    )


def summary_context(db: Session) -> str:
    projects = db.query(Project).order_by(Project.id).all()
    ids = [p.id for p in projects]
    summary = project_summary(db, ids)
    per_project = []
    for p in projects:
        bid = compute_bid(db, [p.id])
        per_project.append(
            {
                "name": p.name,
                "documents": len(p.documents),
                "bid_total": bid["total"],
                "unpriced_material_keys": bid["unpriced_keys"],
                "order_plans": _project_plans(db, p.id),
            }
        )
    return _fit(
        {
            "scope": "all projects combined",
            "projects": per_project,
            "materials_all_projects": {
                "rows": [_slim_summary_row(r) for r in summary["rows"]],
                "totals": summary["totals"],
            },
            "not_included_in_numbers": summary["unreviewed"],
        }
    )
