"""Drawing primitives for synthetic PDF drawings, and the truth they imply.

Ground truth is derived from the DECLARED parameters (a circle of diameter d at (x, y)),
never from reading the drawn geometry back. If both sides read the polygon, a bug in an
emitter would produce matching-but-wrong truth and the case would pass on a garbage
drawing.

Two ink conventions, because `ink.py` chooses between them per page and half the real
drawings take each path:

  colour: part edges black, dimension lines mid-grey, sheet border light grey
  width:  everything black, part edges thick, dimension lines thin
"""

from __future__ import annotations

import math

import fitz

BLACK = (0, 0, 0)
GREY = (0.5, 0.5, 0.5)
LIGHT = (0.75, 0.75, 0.75)

CONVENTIONS = ("colour", "width")


def pen(convention: str, role: str = "geometry") -> dict:
    """Stroke colour and width for a role, under the given drafting convention."""
    if convention == "colour":
        return {
            "geometry": {"color": BLACK, "width": 0.7},
            "annotation": {"color": GREY, "width": 0.7},
            "frame": {"color": LIGHT, "width": 0.7},
        }[role]
    return {
        "geometry": {"color": BLACK, "width": 1.2},
        "annotation": {"color": BLACK, "width": 0.35},
        "frame": {"color": LIGHT, "width": 0.7},
    }[role]


# ---------------------------------------------------------------- point maths


def rotate(pts, cx: float, cy: float, deg: float):
    if not deg:
        return list(pts)
    a = math.radians(deg)
    ca, sa = math.cos(a), math.sin(a)
    return [
        (cx + (x - cx) * ca - (y - cy) * sa, cy + (x - cx) * sa + (y - cy) * ca)
        for x, y in pts
    ]


def circle_pts(cx: float, cy: float, r: float, n: int = 64):
    return [
        (cx + r * math.cos(2 * math.pi * i / n), cy + r * math.sin(2 * math.pi * i / n))
        for i in range(n + 1)
    ]


def rect_pts(cx: float, cy: float, w: float, h: float, deg: float = 0.0):
    hw, hh = w / 2, h / 2
    pts = [
        (cx - hw, cy - hh), (cx + hw, cy - hh),
        (cx + hw, cy + hh), (cx - hw, cy + hh), (cx - hw, cy - hh),
    ]
    return rotate(pts, cx, cy, deg)


def obround_pts(cx: float, cy: float, length: float, width: float, deg: float = 0.0, n: int = 24):
    """A slot: a rectangle capped with semicircles on its short ends."""
    r = width / 2
    straight = max(length - width, 0.0) / 2
    pts = []
    for i in range(n + 1):  # right cap
        a = -math.pi / 2 + math.pi * i / n
        pts.append((cx + straight + r * math.cos(a), cy + r * math.sin(a)))
    for i in range(n + 1):  # left cap
        a = math.pi / 2 + math.pi * i / n
        pts.append((cx - straight + r * math.cos(a), cy + r * math.sin(a)))
    pts.append(pts[0])
    return rotate(pts, cx, cy, deg)


def ngon_pts(cx: float, cy: float, r: float, sides: int, deg: float = 0.0):
    pts = [
        (cx + r * math.cos(2 * math.pi * i / sides), cy + r * math.sin(2 * math.pi * i / sides))
        for i in range(sides + 1)
    ]
    return rotate(pts, cx, cy, deg)


# ---------------------------------------------------------------- freeform families

def freeform_pts(kind: str, cx: float, cy: float, s: float, deg: float = 0.0):
    """Cutouts that are real openings but fit no ideal shape — they must still be
    DETECTED. The pipeline deliberately scores them below auto-approval."""
    u = s / 2
    shapes = {
        "L": [(-u, -u), (u, -u), (u, 0), (0, 0), (0, u), (-u, u)],
        "T": [(-u, -u), (u, -u), (u, -u / 3), (u / 3, -u / 3), (u / 3, u), (-u / 3, u),
              (-u / 3, -u / 3), (-u, -u / 3)],
        "cross": [(-u / 3, -u), (u / 3, -u), (u / 3, -u / 3), (u, -u / 3), (u, u / 3),
                  (u / 3, u / 3), (u / 3, u), (-u / 3, u), (-u / 3, u / 3), (-u, u / 3),
                  (-u, -u / 3), (-u / 3, -u / 3)],
        "zigzag": [(-u, -u), (0, -u / 2), (u, -u), (u, u), (0, u / 2), (-u, u)],
        "wedge": [(-u, -u), (u, -u), (0, u)],
        "arrow": [(-u, -u / 2), (0, -u / 2), (0, -u), (u, 0), (0, u), (0, u / 2),
                  (-u, u / 2)],
    }
    if kind == "keyhole":  # a bulb fused with a narrower stem
        # built by union rather than by hand: a hand-traversed keyhole self-intersects
        # at the stem junction, and the pipeline then correctly sees TWO regions —
        # a defect in the test drawing, not in the detector
        from shapely.geometry import Point as _P
        from shapely.geometry import box as _box
        from shapely.ops import unary_union

        merged = unary_union([_P(0, -u / 2).buffer(u / 2, quad_segs=16), _box(-u / 4, -u / 2, u / 4, u)])
        pts = list(merged.exterior.coords)
    elif kind == "D":  # half-round: flat on one side
        pts = [(x, y) for x, y in circle_pts(0, 0, u, 48) if y >= 0]
        pts.append(pts[0])
    else:
        pts = shapes[kind]
    pts = [(cx + x, cy + y) for x, y in pts]
    pts.append(pts[0])
    return rotate(pts, cx, cy, deg)


FREEFORM_KINDS = ("L", "T", "cross", "zigzag", "wedge", "arrow", "keyhole", "D")


# ---------------------------------------------------------------- notch families

def notch_profile(kind: str, w: float, h: float, notch_w: float, notch_d: float):
    """A w x h plate outline with a bite taken out of its TOP edge, centred.

    A notch is open to the part's edge, so it is never an enclosed loop — it exists
    only as a concavity of the outline, which is how `_notch_candidates` reads it.
    """
    x0, x1 = (w - notch_w) / 2, (w + notch_w) / 2
    top = h
    mouth_l, mouth_r = (x0, top), (x1, top)
    floor = top - notch_d
    if kind == "rect":
        bite = [mouth_l, (x0, floor), (x1, floor), mouth_r]
    elif kind == "obround":
        # a slot cut in from the edge: straight sides, round bottom. Built by
        # subtraction so the profile is exact rather than hand-traversed.
        from shapely.geometry import Polygon as _Poly
        from shapely.geometry import box as _box
        from shapely.ops import unary_union

        r = min(notch_w / 2, notch_d / 2)
        cut = unary_union(
            [
                _box(x0, floor + r, x1, top + 1),
                _Poly(
                    [
                        (w / 2 + r * math.cos(a), floor + r + r * math.sin(a))
                        for a in [math.pi + math.pi * i / 24 for i in range(25)]
                    ]
                ),
            ]
        )
        plate = _box(0, 0, w, top).difference(cut)
        return list(plate.exterior.coords)
    elif kind == "semicircle":
        # a half-ellipse spanning notch_w and reaching notch_d deep, so the requested
        # depth is honoured (a fixed half-disc of radius notch_w/2 would ignore it)
        a, b = notch_w / 2, notch_d
        cx = w / 2
        bite = [mouth_l] + [
            (cx - a * math.cos(math.pi * i / 24), top - b * math.sin(math.pi * i / 24))
            for i in range(25)
        ] + [mouth_r]
    elif kind == "vee":  # a chamfer, NOT a manufactured cut — must be rejected
        bite = [mouth_l, (w / 2, floor), mouth_r]
    elif kind == "stepped":
        bite = [
            mouth_l, (x0, floor + notch_d / 2), (x0 + notch_w / 4, floor + notch_d / 2),
            (x0 + notch_w / 4, floor), (x1, floor), mouth_r,
        ]
    else:
        raise ValueError(kind)
    return [(0, 0), (w, 0), (w, h)] + list(reversed(bite)) + [(0, h), (0, 0)]


NOTCH_KINDS = ("rect", "obround", "semicircle", "vee", "stepped")


# ---------------------------------------------------------------- page drawing


def stroke(page: fitz.Page, pts, convention: str, role: str = "geometry") -> None:
    """One closed contour as ONE path, so it is recoverable as a CAD loop."""
    page.draw_polyline([fitz.Point(*p) for p in pts], **pen(convention, role))


def sheet_frame(page: fitz.Page, convention: str) -> None:
    r = page.rect
    page.draw_rect(
        fitz.Rect(r.x0 + 18, r.y0 + 18, r.x1 - 18, r.y1 - 18), **pen(convention, "frame")
    )
