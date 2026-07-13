"""Group cutouts into BOM rows: one row per (shape, size), with quantity and cut length."""

from shapely import wkt as shapely_wkt

from app.bom.shapes import SHAPE_LABEL, dims_key, dims_label, shape_metrics
from app.db.models import Cutout

# What the operator has accepted. Rejected cutouts stay queryable but never
# contribute quantity or cut length.
ACCEPTED_STATUSES = ("approved", "edited")


def cutout_metrics(c: Cutout) -> dict:
    """Shape / dims / cut length for one cutout, from its effective geometry."""
    geom = shapely_wkt.loads(c.edited_geometry_wkt or c.geometry_wkt)
    return shape_metrics(geom, c.kind)


def build_rows(cutouts: list[Cutout]) -> list[dict]:
    """One row per (shape, size). Quantity counts accepted cutouts only.

    Rows group on the snapped size but report the group's *mean* size, so the
    displayed dimensions and the cut length beside them always agree.
    """
    rows: dict[str, dict] = {}
    for c in cutouts:
        m = cutout_metrics(c)
        key = f"{m['shape']}|{dims_key(m['dims'])}"
        row = rows.setdefault(
            key,
            {
                "key": key,
                "shape": m["shape"],
                "shape_label": SHAPE_LABEL[m["shape"]],
                "qty": 0,
                "cut_length_total_mm": 0.0,
                "cutout_ids": [],
                "rejected_ids": [],
                "pending_qty": 0,
                "_dims_sum": dict.fromkeys(m["dims"], 0.0),
            },
        )
        if c.status == "rejected":
            row["rejected_ids"].append(c.id)
            continue
        row["cutout_ids"].append(c.id)
        row["qty"] += 1
        row["cut_length_total_mm"] += m["cut_length_mm"]
        for k, v in m["dims"].items():
            row["_dims_sum"][k] += v
        if c.status == "pending":
            row["pending_qty"] += 1

    for row in rows.values():
        qty = row["qty"]
        total = row["cut_length_total_mm"]
        dims_sum = row.pop("_dims_sum")
        row["dims"] = dims_label({k: v / qty for k, v in dims_sum.items()}) if qty else "—"
        row["cut_length_total_mm"] = round(total, 2)
        row["cut_length_each_mm"] = round(total / qty, 2) if qty else 0.0

    # rows with no accepted members (everything rejected) carry no quantity
    return sorted(
        (r for r in rows.values() if r["qty"] or r["rejected_ids"]),
        key=lambda r: (-r["qty"], r["shape"], r["dims"]),
    )


def totals(rows: list[dict]) -> dict:
    return {
        "qty": sum(r["qty"] for r in rows),
        "cut_length_mm": round(sum(r["cut_length_total_mm"] for r in rows), 2),
        "pending_qty": sum(r["pending_qty"] for r in rows),
    }
