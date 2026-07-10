import math
from dataclasses import dataclass

import fitz
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import polygonize, unary_union

BEZIER_SAMPLES = 8
SNAP_DECIMALS = 2
# a polygon covering this much of the page is the drawing frame, not a part
FRAME_AREA_RATIO = 0.5
MIN_CUTOUT_AREA_PT2 = 4.0
MAX_CUTOUT_PARENT_RATIO = 0.6
CIRCLE_FIT_THRESHOLD = 0.90
RECT_FIT_THRESHOLD = 0.95
# concentric double-strokes (hole + countersink/centermark circle) yield
# IoU = A_inner/A_outer ~ 0.45-0.9; distinct real cutouts never overlap
DUPLICATE_IOU = 0.40

PT_TO_MM = 25.4 / 72


@dataclass
class Candidate:
    polygon: Polygon
    kind: str  # hole | slot | freeform
    shape_fit: float  # 0-1 quality of the circle/rect fit
    parent_area: float
    measured_dims: dict
    contains_text: bool = False
    from_loop: bool = False  # backed by a single closed CAD path, not just a planar face
    source: str = "vector"  # vector | raster_cv
    dimension_text: str | None = None


def _pt(p, m: fitz.Matrix) -> tuple[float, float]:
    q = fitz.Point(p.x, p.y) * m
    return (round(q.x, SNAP_DECIMALS), round(q.y, SNAP_DECIMALS))


def _sample_bezier(p0, p1, p2, p3, m: fitz.Matrix) -> list[tuple[float, float]]:
    pts = []
    for i in range(BEZIER_SAMPLES + 1):
        t = i / BEZIER_SAMPLES
        mt = 1 - t
        x = mt**3 * p0.x + 3 * mt**2 * t * p1.x + 3 * mt * t**2 * p2.x + t**3 * p3.x
        y = mt**3 * p0.y + 3 * mt**2 * t * p1.y + 3 * mt * t**2 * p2.y + t**3 * p3.y
        pts.append(_pt(fitz.Point(x, y), m))
    return pts


def _is_dashed(path: dict) -> bool:
    dashes = path.get("dashes")
    return bool(dashes) and dashes != "[] 0"


LOOP_CLOSE_TOL_PT = 1.5
# 4*pi*A/P^2: kills force-closed slivers (dimension/leader lines) while keeping
# thin-but-real slots (a 10:1 rectangle scores ~0.26)
LOOP_MIN_THINNESS = 0.15


def _item_points(item, m: fitz.Matrix) -> list[tuple[float, float]]:
    op = item[0]
    if op == "l":
        return [_pt(item[1], m), _pt(item[2], m)]
    if op == "c":
        return _sample_bezier(*item[1:5], m)
    if op == "re":
        r = item[1]
        corners = [r.tl, r.tr, r.br, r.bl, r.tl]
        return [_pt(p, m) for p in corners]
    if op == "qu":
        q = item[1]
        return [_pt(q.ul, m), _pt(q.ur, m), _pt(q.lr, m), _pt(q.ll, m), _pt(q.ul, m)]
    return []


def _dist(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _is_construction(path: dict) -> bool:
    # fill-only paths are annotation solids (dimension arrowheads, symbols,
    # glyph shapes) — cutout contours are always stroked
    return path.get("type") == "f" or _is_dashed(path)


def _path_loops(page: fitz.Page) -> list[Polygon]:
    """Closed loops recovered per path.

    CAD exports often draw a circle/contour as one polyline path that does not
    numerically close; polygonize() silently drops such dangling rings, so we
    chain each path's items and force-close small gaps.
    """
    loops = []
    # get_drawings() coords are in unrotated page space; renders/text are in
    # rotated space — rotation_matrix maps the former to the latter
    m = page.rotation_matrix
    for path in page.get_drawings():
        if _is_construction(path):
            continue
        chains: list[list[tuple[float, float]]] = []
        current: list[tuple[float, float]] = []
        for item in path["items"]:
            pts = _item_points(item, m)
            if not pts:
                continue
            if current and _dist(current[-1], pts[0]) <= LOOP_CLOSE_TOL_PT:
                current.extend(pts[1:])
            else:
                if len(current) >= 4:
                    chains.append(current)
                current = pts
        if len(current) >= 4:
            chains.append(current)

        for chain in chains:
            perimeter = sum(_dist(a, b) for a, b in zip(chain, chain[1:]))
            gap = _dist(chain[0], chain[-1])
            if gap > min(LOOP_CLOSE_TOL_PT, perimeter * 0.05):
                continue
            try:
                poly = Polygon(chain)
            except Exception:
                continue
            if not poly.is_valid or poly.area < MIN_CUTOUT_AREA_PT2:
                continue
            thinness = 4 * math.pi * poly.area / poly.exterior.length**2
            if thinness >= LOOP_MIN_THINNESS:
                loops.append(poly)
    return loops


def _segments(page: fitz.Page) -> list[LineString]:
    segs = []
    m = page.rotation_matrix
    for path in page.get_drawings():
        # dashed/fill-only paths are construction geometry and annotation
        # solids; they fragment real contours in the planar arrangement
        if _is_construction(path):
            continue
        for item in path["items"]:
            pts = _item_points(item, m)
            if len(set(pts)) >= 2:
                segs.append(LineString(pts))
    return segs


def _iou(a: Polygon, b: Polygon) -> float:
    try:
        inter = a.intersection(b).area
        union = a.union(b).area
        return inter / union if union else 0.0
    except Exception:
        return 0.0


def _dedupe(shells: list[tuple[Polygon, bool]]) -> list[tuple[Polygon, bool]]:
    kept: list[tuple[Polygon, bool]] = []
    for p, from_loop in shells:  # sorted by area desc, loops preferred on ties
        pb = p.bounds
        dup = False
        for k, _ in kept:
            kb = k.bounds
            if pb[0] > kb[2] or pb[2] < kb[0] or pb[1] > kb[3] or pb[3] < kb[1]:
                continue
            if _iou(p, k) > DUPLICATE_IOU:
                dup = True
                break
        if not dup:
            kept.append((p, from_loop))
    return kept


def _classify(poly: Polygon) -> tuple[str, float, dict]:
    area = poly.area
    perimeter = poly.exterior.length
    circularity = 4 * math.pi * area / perimeter**2 if perimeter else 0.0

    if circularity >= CIRCLE_FIT_THRESHOLD:
        diameter_pt = 2 * math.sqrt(area / math.pi)
        dims = {"diameter_mm": round(diameter_pt * PT_TO_MM, 2)}
        return "hole", circularity, dims

    mrr = poly.minimum_rotated_rectangle
    rect_fit = area / mrr.area if isinstance(mrr, Polygon) and mrr.area else 0.0
    if rect_fit >= RECT_FIT_THRESHOLD:
        coords = list(mrr.exterior.coords)
        side_a = LineString(coords[0:2]).length
        side_b = LineString(coords[1:3]).length
        dims = {
            "length_mm": round(max(side_a, side_b) * PT_TO_MM, 2),
            "width_mm": round(min(side_a, side_b) * PT_TO_MM, 2),
        }
        return "slot", rect_fit, dims

    b = poly.bounds
    dims = {
        "bbox_w_mm": round((b[2] - b[0]) * PT_TO_MM, 2),
        "bbox_h_mm": round((b[3] - b[1]) * PT_TO_MM, 2),
    }
    return "freeform", max(circularity, rect_fit), dims


def build_candidates(
    tagged: list[tuple[Polygon, bool]],
    page_area: float,
    text_centers: list[tuple[float, float]],
    source: str = "vector",
) -> list[Candidate]:
    """Shared hierarchy + classification over closed shapes from any pipeline.

    tagged: (polygon, from_loop) pairs in page-point coordinates.
    """
    if not tagged:
        return []

    # hierarchy is computed on exterior shells: planar faces mean a part with
    # holes does not .contain() the hole faces directly
    shells = sorted(
        ((Polygon(p.exterior), fl) for p, fl in tagged),
        key=lambda t: (-t[0].area, not t[1]),
    )
    shells = _dedupe(shells)

    inner = [
        (s, fl) for s, fl in shells if s.area < FRAME_AREA_RATIO * page_area
    ]

    # parent = smallest non-frame shell strictly containing the candidate
    candidates: list[Candidate] = []
    for s, from_loop in inner:
        parent = None
        for other, _ in reversed(inner):  # smallest first
            if other.area <= s.area:
                continue
            if other.contains(s.representative_point()):
                parent = other
                break
        if parent is None:
            continue  # part outline, not a cutout
        if s.area > parent.area * MAX_CUTOUT_PARENT_RATIO:
            continue
        kind, shape_fit, dims = _classify(s)
        b = s.bounds
        contains_text = any(
            b[0] <= cx <= b[2] and b[1] <= cy <= b[3] and s.contains(Point(cx, cy))
            for cx, cy in text_centers
        )
        candidates.append(
            Candidate(
                polygon=s,
                kind=kind,
                shape_fit=shape_fit,
                parent_area=parent.area,
                measured_dims=dims,
                contains_text=contains_text,
                from_loop=from_loop,
                source=source,
            )
        )
    return candidates


def extract_candidates(page: fitz.Page) -> list[Candidate]:
    segs = _segments(page)
    if not segs:
        return []

    # text word centers mark annotation boxes (title block cells, dimension frames)
    text_centers = [
        ((w[0] + w[2]) / 2, (w[1] + w[3]) / 2) for w in page.get_text("words")
    ]

    faces = [
        p
        for p in polygonize(unary_union(segs))
        if p.is_valid and p.area >= MIN_CUTOUT_AREA_PT2
    ]
    tagged = [(p, False) for p in faces] + [(p, True) for p in _path_loops(page)]
    return build_candidates(tagged, abs(page.rect), text_centers)
