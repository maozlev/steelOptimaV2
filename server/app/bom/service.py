"""Group cutouts into BOM rows: one row per (shape, size), with quantity and cut length."""

import json

from shapely import wkt as shapely_wkt

from app.bom.shapes import SHAPE_LABEL, dims_label, shape_metrics
from app.config import settings
from app.db.models import Cutout

# What the operator has accepted. Rejected cutouts stay queryable but never
# contribute quantity or cut length.
ACCEPTED_STATUSES = ("approved", "edited")

# Two cutouts belong in the same BOM row when every dimension agrees this closely.
# Doc_HK3573's 16 bolt holes measure 12.25-12.40mm — a 1.2% spread from CV noise — and
# must land in one row, not two.
SIZE_TOLERANCE = 0.03


def cutout_metrics(c: Cutout, scale: float | None = None) -> dict:
    """Shape / dims / cut length for one cutout, from its effective geometry.

    `scale` converts paper millimetres to real ones (a 1:5 sheet is 5.0). Without it the
    numbers are the size of the ink on the page, not the size of the part — the gear's
    Ø290 bore measures Ø82.9 of paper. Callers that have a page MUST pass its scale.
    """
    geom = shapely_wkt.loads(c.edited_geometry_wkt or c.geometry_wkt)
    # A notch's mouth is open to the part's edge and never burned; the detector
    # measured the true cut side at extraction time. Editing the geometry
    # invalidates that measurement, so fall back to the perimeter then.
    hint = None
    if c.kind == "notch" and not c.edited_geometry_wkt and c.measured_dims_json:
        hint = json.loads(c.measured_dims_json).get("cut_length_mm")
    m = shape_metrics(geom, c.kind, cut_hint_mm=hint)
    if scale is None or scale == 1.0:
        return m
    return {
        "shape": m["shape"],
        "dims": {k: round(v * scale, 2) for k, v in m["dims"].items()},
        "cut_length_mm": round(m["cut_length_mm"] * scale, 2),
    }


def page_scales(db, cutouts: list[Cutout]) -> dict[int, float | None]:
    """page_id -> scale, for every page these cutouts live on."""
    from app.db.models import Page

    page_ids = {c.page_id for c in cutouts}
    if not page_ids:
        return {}
    return {
        p.id: p.scale for p in db.query(Page).filter(Page.id.in_(page_ids)).all()
    }


def _cluster_sizes(metrics: list[dict]) -> list[str]:
    """A grouping key per cutout, from sizes that agree with EACH OTHER.

    Not a grid. Snapping to a fixed 0.5mm grid put Doc_HK3573's 16 identical bolt holes
    — which measure 12.25 to 12.40mm — across a bucket boundary at 12.25, and Python's
    banker's rounding sent that one hole to 12.0 while its fifteen siblings went to 12.5.
    One hole type, split into two BOM rows, by a rounding rule. Any fixed grid has
    boundaries and something will eventually land on one.

    Sizes are clustered against each other instead: two cutouts share a row when every
    dimension agrees within SIZE_TOLERANCE. Single-linkage, largest-first, so the biggest
    population of a size anchors its own cluster.
    """
    def dims_of(i: int) -> tuple[float, ...]:
        d = metrics[i]["dims"]
        return tuple(d[k] for k in sorted(d))

    def alike(a: tuple[float, ...], b: tuple[float, ...]) -> bool:
        return len(a) == len(b) and all(
            abs(x - y) <= SIZE_TOLERANCE * max(abs(x), abs(y), 1e-9)
            for x, y in zip(a, b)
        )

    assigned: dict[int, str] = {}
    for shape in {m["shape"] for m in metrics}:
        members = sorted(
            (i for i, m in enumerate(metrics) if m["shape"] == shape),
            key=lambda i: sum(dims_of(i)),
        )
        # a sorted sweep, chaining each cutout onto the previous one it agrees with.
        # Comparing only against a cluster's first member would leave 4.9 and 5.1 in
        # separate rows even though both agree with the 5.0 between them.
        key = None
        prev: tuple[float, ...] | None = None
        for i in members:
            dims = dims_of(i)
            if prev is None or not alike(prev, dims):
                key = f"{shape}|{'x'.join(f'{v:.3f}' for v in dims)}"
            assigned[i] = key
            prev = dims

    return [assigned[i] for i in range(len(metrics))]


def build_rows(cutouts: list[Cutout], scales: dict[int, float | None] | None = None) -> list[dict]:
    """One row per (shape, size). Quantity counts accepted cutouts only.

    Rows group sizes that agree with each other, and report the group's *mean* size, so
    the displayed dimensions and the cut length beside them always agree.
    """
    metrics = [cutout_metrics(c, (scales or {}).get(c.page_id)) for c in cutouts]
    keys = _cluster_sizes(metrics)

    rows: dict[str, dict] = {}
    for c, m, key in zip(cutouts, metrics, keys):
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
                "confident_qty": 0,
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
            if c.confidence >= settings.finalize_threshold:
                row["confident_qty"] += 1
        else:
            row["confident_qty"] += 1  # a human already decided this one

    for row in rows.values():
        qty = row["qty"]
        total = row["cut_length_total_mm"]
        dims_sum = row.pop("_dims_sum")
        row["dims"] = dims_label({k: v / qty for k, v in dims_sum.items()}) if qty else "—"
        row["cut_length_total_mm"] = round(total, 2)
        row["cut_length_each_mm"] = round(total / qty, 2) if qty else 0.0
        # A row where nothing clears the finalize threshold is not part of the work order:
        # finalize will auto-reject every one of its members. It is still SHOWN — a missed
        # hole costs a part, so nothing is hidden — but it belongs under "needs review",
        # not listed among things to cut.
        row["needs_review"] = row["confident_qty"] == 0

    # rows with no accepted members (everything rejected) carry no quantity
    return sorted(
        (r for r in rows.values() if r["qty"] or r["rejected_ids"]),
        key=lambda r: (r["needs_review"], -r["qty"], r["shape"], r["dims"]),
    )


def totals(rows: list[dict]) -> dict:
    return {
        "qty": sum(r["qty"] for r in rows),
        "cut_length_mm": round(sum(r["cut_length_total_mm"] for r in rows), 2),
        "pending_qty": sum(r["pending_qty"] for r in rows),
    }
