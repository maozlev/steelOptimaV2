"""Project-level materials summary: pooled approved rows grouped by material key.

Same discipline as bom/service.py: pool first, group once, so the project view,
the cross-project rollup and the bid all read the same numbers. A cross-project
rollup is just this function with several project ids — no extra entity.

Only human-trustworthy rows count: row status auto_approved/approved/edited AND
table approved. The response also carries what is NOT counted (pending tables,
flagged rows) so a summary is never silently partial.
"""

from collections import Counter

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models import Document, MaterialRow, MaterialTable, Page, Project

INCLUDED_ROW_STATUSES = ("auto_approved", "approved", "edited")


def project_summary(db: Session, project_ids: list[int]) -> dict:
    projects = (
        db.query(Project).filter(Project.id.in_(project_ids)).all()
        if project_ids
        else []
    )
    base = (
        db.query(MaterialRow, MaterialTable, Page, Document)
        .join(MaterialTable, MaterialRow.table_id == MaterialTable.id)
        .join(Page, MaterialTable.page_id == Page.id)
        .join(Document, Page.document_id == Document.id)
        .filter(Document.project_id.in_(project_ids))
    )
    included = base.filter(
        MaterialTable.status == "approved",
        MaterialRow.status.in_(INCLUDED_ROW_STATUSES),
    ).all()

    project_names = {p.id: p.name for p in projects}
    groups: dict[str, dict] = {}
    for row, _table, _page, doc in included:
        key = row.material_key or "(unidentified)"
        g = groups.setdefault(
            key,
            {
                "material_key": key,
                "descriptions": Counter(),
                "qty": 0.0,
                "total_length_mm": 0.0,
                "total_weight_kg": 0.0,
                "total_area_m2": 0.0,  # plates; stays 0 for bars
                "lengths": Counter(),  # unit_length_mm -> qty
                "documents": set(),
                "projects": set(),
                "row_ids": [],
            },
        )
        qty = row.qty or 0.0
        g["qty"] += qty
        if row.description:
            g["descriptions"][row.description] += 1
        length = row.total_length_mm
        if length is None and row.unit_length_mm is not None and qty:
            length = qty * row.unit_length_mm
        if length:
            g["total_length_mm"] += length
        if row.total_weight_kg:
            g["total_weight_kg"] += row.total_weight_kg
        if row.area_m2:
            g["total_area_m2"] += row.area_m2
        if row.unit_length_mm and qty:
            g["lengths"][row.unit_length_mm] += int(qty)
        g["documents"].add(doc.filename)
        g["projects"].add(project_names.get(doc.project_id, str(doc.project_id)))
        g["row_ids"].append(row.id)

    rows = []
    for g in sorted(groups.values(), key=lambda x: -x["total_weight_kg"]):
        rows.append(
            {
                "material_key": g["material_key"],
                "description": (
                    g["descriptions"].most_common(1)[0][0]
                    if g["descriptions"]
                    else None
                ),
                "qty": g["qty"],
                "total_length_mm": round(g["total_length_mm"], 1),
                "total_weight_kg": round(g["total_weight_kg"], 2),
                "total_area_m2": round(g["total_area_m2"], 4),
                "lengths": [
                    {"unit_length_mm": length, "qty": qty}
                    for length, qty in sorted(g["lengths"].items())
                ],
                "documents": sorted(g["documents"]),
                "projects": sorted(g["projects"]),
                "row_ids": g["row_ids"],
            }
        )

    # what is NOT in the numbers above — the summary must never look complete
    # while a real material table is still pending. Only "materials" tables count:
    # the operator has chosen to review those alone, so "unknown"/"other" grids
    # (title-block fragments, fastener schedules, stray rulings) are neither
    # surfaced for review nor allowed to hold the summary open. Trade-off: a
    # materials table misclassified as "unknown" is silently excluded — the price
    # of a review queue that only ever shows real material tables.
    pending_tables = (
        base.filter(
            MaterialTable.status == "pending",
            MaterialTable.kind == "materials",
        )
        .with_entities(func.count(func.distinct(MaterialTable.id)))
        .scalar()
        or 0
    )
    flagged_rows = base.filter(
        MaterialTable.status != "rejected",
        MaterialTable.kind == "materials",
        MaterialRow.status == "needs_review",
    ).count()

    return {
        "projects": [{"id": p.id, "name": p.name} for p in projects],
        "rows": rows,
        "totals": {
            "qty": sum(r["qty"] for r in rows),
            "total_weight_kg": round(sum(r["total_weight_kg"] for r in rows), 2),
            "total_length_mm": round(sum(r["total_length_mm"] for r in rows), 1),
        },
        "unreviewed": {
            "pending_tables": pending_tables,
            "needs_review_rows": flagged_rows,
        },
    }
