"""Bid computation: summary rows x the user's prices.

The user picks the pricing unit PER LINE (per kg / per m / per unit) — sellers
quote profiles by weight, bars by meter and plates by piece, sometimes in the
same bid. Unpriced lines are returned loudly and excluded from the total; a bid
that silently zero-prices a material is worse than no bid.
"""

from sqlalchemy.orm import Session

from app.db.models import MaterialPrice
from app.tables.aggregate import project_summary

PRICING_UNITS = ("per_kg", "per_m", "per_unit")


def upsert_prices(db: Session, project_id: int | None, entries: list[dict]) -> int:
    """Bulk upsert on (project_id, material_key). Returns rows written."""
    existing = {
        p.material_key: p
        for p in db.query(MaterialPrice).filter(
            MaterialPrice.project_id.is_(None)
            if project_id is None
            else MaterialPrice.project_id == project_id
        )
    }
    written = 0
    for entry in entries:
        key = entry["material_key"]
        row = existing.get(key)
        if row is None:
            row = MaterialPrice(project_id=project_id, material_key=key)
            db.add(row)
            existing[key] = row
        row.price = float(entry["price"])
        row.pricing_unit = entry["pricing_unit"]
        written += 1
    db.commit()
    return written


def _price_lookup(db: Session, project_ids: list[int]) -> dict[str, MaterialPrice]:
    """Project price beats the global price book (project_id NULL)."""
    lookup: dict[str, MaterialPrice] = {}
    for p in db.query(MaterialPrice).filter(MaterialPrice.project_id.is_(None)):
        lookup[p.material_key] = p
    for p in db.query(MaterialPrice).filter(MaterialPrice.project_id.in_(project_ids)):
        lookup[p.material_key] = p
    return lookup


def compute_bid(db: Session, project_ids: list[int]) -> dict:
    summary = project_summary(db, project_ids)
    prices = _price_lookup(db, project_ids)

    rows = []
    total = 0.0
    unpriced = []
    for row in summary["rows"]:
        price_row = prices.get(row["material_key"])
        line = {
            **row,
            "price": price_row.price if price_row else None,
            "pricing_unit": price_row.pricing_unit if price_row else None,
            "line_total": None,
        }
        if price_row:
            basis = {
                "per_kg": row["total_weight_kg"],
                "per_m": row["total_length_mm"] / 1000.0,
                "per_unit": row["qty"],
            }[price_row.pricing_unit]
            line["line_total"] = round(basis * price_row.price, 2)
            total += line["line_total"]
        else:
            unpriced.append(row["material_key"])
        rows.append(line)

    return {
        "projects": summary["projects"],
        "rows": rows,
        "total": round(total, 2),
        "unpriced_keys": unpriced,
        "unreviewed": summary["unreviewed"],
    }
