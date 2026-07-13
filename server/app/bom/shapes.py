"""Fabricator-facing shape, dimensions and cut length, derived from geometry.

The DB `kind` enum (hole/slot/notch/freeform) conflates a true rectangle with an
obround slot — both clear RECT_FIT_THRESHOLD, so both are stored as "slot". A BOM
has to tell them apart: they have different cut lengths and different tooling. So
the shape shown to the operator is derived here from the effective geometry rather
than read off `kind`.

Cut length is the burn distance around the cutout. It is computed from the ideal
shape, not the polygon perimeter: a CAD circle is a many-segment polyline and a
snapped raster circle is a 16-gon, and both under-measure a true circumference.
"""

import math

from shapely.geometry import LineString, Polygon

from app.extraction.vector import (
    CIRCLE_FIT_THRESHOLD,
    PT_TO_MM,
    RECT_FIT_THRESHOLD,
)

CIRCLE = "circle"
RECTANGLE = "rectangle"
SLOT = "slot"
NOTCH = "notch"
IRREGULAR = "irregular"

SHAPE_LABEL = {
    CIRCLE: "Circle",
    RECTANGLE: "Rectangle",
    SLOT: "Slot",
    NOTCH: "Notch",
    IRREGULAR: "Irregular",
}


def _mrr_sides_mm(poly: Polygon) -> tuple[float, float] | None:
    """Long and short side of the minimum rotated rectangle, in mm."""
    mrr = poly.minimum_rotated_rectangle
    if not isinstance(mrr, Polygon) or not mrr.area:
        return None
    coords = list(mrr.exterior.coords)
    a = LineString(coords[0:2]).length
    b = LineString(coords[1:3]).length
    return max(a, b) * PT_TO_MM, min(a, b) * PT_TO_MM


def shape_metrics(poly: Polygon, kind: str | None = None) -> dict:
    """Derive shape, dimensions (mm) and cut length (mm) for one cutout.

    `kind` is only consulted for "notch", which CV never emits — it can only come
    from a human or the VLM, so it is an explicit label worth preserving.
    """
    area_pt = poly.area
    perimeter_pt = poly.exterior.length
    fallback = {
        "shape": IRREGULAR,
        "dims": _bbox_dims(poly),
        "cut_length_mm": round(perimeter_pt * PT_TO_MM, 2),
    }
    if kind == NOTCH:
        return {**fallback, "shape": NOTCH}
    if not perimeter_pt or not area_pt:
        return fallback

    circularity = 4 * math.pi * area_pt / perimeter_pt**2
    if circularity >= CIRCLE_FIT_THRESHOLD:
        diameter = 2 * math.sqrt(area_pt / math.pi) * PT_TO_MM
        return {
            "shape": CIRCLE,
            "dims": {"diameter_mm": round(diameter, 2)},
            "cut_length_mm": round(math.pi * diameter, 2),
        }

    sides = _mrr_sides_mm(poly)
    if sides is None:
        return fallback
    length, width = sides
    area_mm = area_pt * PT_TO_MM**2
    rect_area = length * width
    if not rect_area or area_mm / rect_area < RECT_FIT_THRESHOLD:
        return fallback

    # An obround fills its bounding rectangle everywhere except the four corners
    # the rounded ends cut away. Whichever ideal area the polygon lands nearer to
    # is the shape it actually is.
    obround_area = rect_area - (4 - math.pi) * (width / 2) ** 2
    dims = {"length_mm": round(length, 2), "width_mm": round(width, 2)}
    if abs(area_mm - rect_area) <= abs(area_mm - obround_area):
        return {
            "shape": RECTANGLE,
            "dims": dims,
            "cut_length_mm": round(2 * (length + width), 2),
        }
    return {
        "shape": SLOT,
        "dims": dims,
        "cut_length_mm": round(2 * (length - width) + math.pi * width, 2),
    }


def _bbox_dims(poly: Polygon) -> dict:
    x0, y0, x1, y1 = poly.bounds
    return {
        "bbox_w_mm": round((x1 - x0) * PT_TO_MM, 2),
        "bbox_h_mm": round((y1 - y0) * PT_TO_MM, 2),
    }


GROUP_SNAP_MM = 0.5


def _snap(v: float) -> float:
    return round(v / GROUP_SNAP_MM) * GROUP_SNAP_MM


def dims_key(dims: dict) -> str:
    """Grouping key: sizes snapped to GROUP_SNAP_MM, so measurement noise does not
    split what is obviously one hole type into a dozen near-identical BOM rows."""
    return dims_label({k: _snap(v) for k, v in dims.items()})


def dims_label(dims: dict) -> str:
    """Human-readable size. Displayed unsnapped, so that it always reconciles with
    the cut length beside it: a row labelled Ø 5.2 mm must have a cut length of
    pi x 5.2. Rounding the label but not the length is how an operator learns to
    distrust the table."""
    if "diameter_mm" in dims:
        return f"Ø {dims['diameter_mm']:.1f} mm"
    if "length_mm" in dims:
        return f"{dims['length_mm']:.1f}×{dims['width_mm']:.1f} mm"
    return f"~{dims['bbox_w_mm']:.1f}×{dims['bbox_h_mm']:.1f} mm"
