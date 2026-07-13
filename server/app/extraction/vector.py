import math
from dataclasses import dataclass

import fitz
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import polygonize, unary_union

from app.extraction.ink import split_ink

BEZIER_SAMPLES = 8
SNAP_DECIMALS = 2
# a polygon covering this much of the page is the drawing frame, not a part
FRAME_AREA_RATIO = 0.5
MIN_CUTOUT_AREA_PT2 = 4.0
# A cutout can fill most of its part: Doc_HK3573 is a gasket whose Ø605 bore is 78% of
# the Ø686 ring around it. The old cap of 0.6 rejected it outright. What this still has
# to reject is a double-stroked part outline, which is ~99% of itself.
MAX_CUTOUT_PARENT_RATIO = 0.90
CIRCLE_FIT_THRESHOLD = 0.90
RECT_FIT_THRESHOLD = 0.95
# A rectangle fills its bounding box (1.00); an obround fills 1 - 0.2146*(W/L), which
# bottoms out at 0.785 as W approaches L. Anything at or above that is a rectangle or a
# slot; below it the shape resembles neither. The old 0.95 gate admitted only skinny
# slots and dropped fat ones into "freeform", where the penalty auto-rejected them.
SLOT_FIT_THRESHOLD = 0.90
# An annotation box is roughly text-sized: a title-block cell or a dimension frame is
# ~10-40pt across. A bore with its own label inside it is far bigger (ASH's Ø290 bore
# is 234pt), and must not be mistaken for a box that exists to hold text.
TEXT_BOX_MAX_SPAN_PT = 40.0
# two shells overlapping more than this are the same outline drawn twice...
# A part is BIG. A top-level loop smaller than this fraction of the biggest one is a
# title-block symbol, not a part — it must not be allowed to admit its own innards.
MIN_PART_AREA_FRAC = 0.02
DUPLICATE_IOU = 0.40
# ...unless one is genuinely NESTED inside the other and materially smaller, in which
# case it is a real cutout: a gasket's bore fills 78% of its ring and must survive.
NESTED_MAX_RATIO = 0.95
# A notch (a bite out of the outline, open to the part's edge) must be a meaningful
# fraction of its part. What this separates it from is the part's OWN shape, which is
# never a cutout: a gear tooth gap is ~0.2% of the gear, the flange's real notch is 14%
# of its part. Shape fit alone also separates them (teeth ~0.6, the notch 0.97), but a
# hull sliver along a near-straight arc can accidentally fit a rectangle, so both gates
# are kept.
NOTCH_MIN_HOST_FRAC = 0.01

PT_TO_MM = 25.4 / 72


@dataclass
class Candidate:
    polygon: Polygon
    kind: str  # hole | slot | notch | freeform
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


def _path_loops(page: fitz.Page, paths: list[dict]) -> list[Polygon]:
    """Closed loops recovered per path.

    CAD exports often draw a circle/contour as one polyline path that does not
    numerically close; polygonize() silently drops such dangling rings, so we
    chain each path's items and force-close small gaps.
    """
    loops = []
    # get_drawings() coords are in unrotated page space; renders/text are in
    # rotated space — rotation_matrix maps the former to the latter
    m = page.rotation_matrix
    for path in paths:
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


def _segments(page: fitz.Page, paths: list[dict]) -> list[LineString]:
    segs = []
    m = page.rotation_matrix
    for path in paths:
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
    """Drop shells that are a second stroke of one already kept.

    Overlap alone does not mean duplication: a hole NESTED inside a shape overlaps it
    heavily and is still a real, distinct cutout. Doc_HK3573 is a gasket whose Ø605 bore
    sits inside its Ø686 ring — an IoU of 0.78 — and the old rule deleted the bore as a
    duplicate of the ring. The system was throwing away the central hole precisely
    because the part is a ring.

    A genuine double-stroke is the SAME outline drawn twice, so it fills nearly all of
    what it overlaps. That is the only thing dropped here.
    """
    kept: list[tuple[Polygon, bool]] = []
    for p, from_loop in shells:  # sorted by area desc, loops preferred on ties
        pb = p.bounds
        dup = False
        for k, _ in kept:
            kb = k.bounds
            if pb[0] > kb[2] or pb[2] < kb[0] or pb[1] > kb[3] or pb[3] < kb[1]:
                continue
            if _iou(p, k) <= DUPLICATE_IOU:
                continue
            # nested (a bore in a ring, a hole in a countersink), not restroked
            if k.contains(p.representative_point()) and p.area <= NESTED_MAX_RATIO * k.area:
                continue
            dup = True
            break
        if not dup:
            kept.append((p, from_loop))
    return kept


def _part_outlines(inner: list[tuple[Polygon, bool]]) -> list[Polygon]:
    """The outlines of the parts drawn on this sheet.

    A cutout is cut out of the PART. Anything outside every part is on the paper, not in
    the metal — Doc_HK3573's "First Angle Projection" symbol is two concentric circles and
    scores 0.98 as a hole, and no amount of geometry will ever say otherwise. WHERE it sits
    is what makes it not a hole.

    A part outline is a closed CAD loop that lies inside no other loop AND IS BIG. The size
    test is what makes this work at all: an earlier version without it failed, because
    those title-block symbols are themselves top-level loops with nothing around them, so
    they qualified as parts and cheerfully admitted themselves. A Ø20mm symbol is not a
    part on a sheet whose part is Ø686.

    Planar faces are deliberately not eligible: the sheet border, the title-block grid and
    the space between dimension lines all polygonize into large faces, any of which would
    "contain" the whole page and make this a no-op.
    """
    loops = [s for s, from_loop in inner if from_loop]
    if not loops:
        return []
    top = [
        s
        for s in loops
        if not any(
            o is not s and o.area > s.area and o.contains(s.representative_point())
            for o in loops
        )
    ]
    if not top:
        return []

    biggest = max(s.area for s in top)
    return [
        s
        for s in top
        # big enough to be a part, and not a title-block symbol
        if s.area >= MIN_PART_AREA_FRAC * biggest
        # AND it actually has something in it. A part CONTAINS cutouts; a shape with
        # nothing inside it is not a part. Without this, 12562 — whose octagonal outline
        # is only a planar face, never a closed loop — declared its own two SLOTS to be
        # "parts", and then threw away the slot that did not happen to sit inside the
        # other one. A part outline we cannot see is better than a part outline we invent:
        # when this finds nothing, the filter simply does not run.
        and any(
            o is not s and o.area < s.area and s.contains(o.representative_point())
            for o, _ in inner
        )
    ]


def _notch_hosts(inner: list[tuple[Polygon, bool]]) -> list[Polygon]:
    """Shells whose outline is worth checking for notches.

    Unlike _part_outlines, planar faces ARE eligible: a part whose profile never closes
    into a single CAD loop (the flange, 12562's octagon) exists only as a face, and the
    flange's notch is exactly the case this has to catch. The other conditions still
    hold — top-level, big, and containing something — so title-block cells and the gaps
    between dimension lines stay out.
    """
    if not inner:
        return []
    biggest = max(s.area for s, _ in inner)
    hosts = []
    for s, _ in inner:
        if s.area < MIN_PART_AREA_FRAC * biggest:
            continue
        if any(
            o is not s and o.area > s.area and o.contains(s.representative_point())
            for o, _ in inner
        ):
            continue
        # a part CONTAINS cutouts; a shape with nothing inside it is not a part
        if not any(
            o is not s and o.area < s.area and s.contains(o.representative_point())
            for o, _ in inner
        ):
            continue
        hosts.append(s)
    return hosts


def _notch_candidates(inner: list[tuple[Polygon, bool]], source: str) -> list[Candidate]:
    """Notches: cutouts OPEN to the part's edge, invisible to enclosed-loop detection.

    A notch is a bite out of the part's outline — a concavity (convex hull minus the
    outline) that is itself shaped like a manufactured cut, a rectangle or an obround.
    That second clause is what separates it from the part's own shape, which is never a
    cutout: gear teeth are concavities too, but tiny and poorly rectangular (~0.6), and
    A (3)'s tapered beam ends are big but triangular (rect fit 0.50). The flange's real
    340x100 notch is 14% of its part at rect fit 0.97.
    """
    out: list[Candidate] = []
    for host in _notch_hosts(inner):
        hull = host.convex_hull
        diff = hull.difference(host)
        pieces = list(diff.geoms) if diff.geom_type == "MultiPolygon" else [diff]
        for piece in pieces:
            if piece.is_empty or piece.geom_type != "Polygon":
                continue
            if piece.area < max(MIN_CUTOUT_AREA_PT2, NOTCH_MIN_HOST_FRAC * host.area):
                continue
            fits = _box_fits(piece)
            if fits is None:
                continue
            rect_fit, obround_fit, length, width = fits
            fit = max(rect_fit, obround_fit)
            if fit < SLOT_FIT_THRESHOLD:
                continue
            # duplicate hosts (a closed loop and its own planar face) bite twice
            if any(_iou(piece, c.polygon) > DUPLICATE_IOU for c in out):
                continue
            # The burn is the part-edge side only. The mouth — where the piece lies on
            # the hull — is open air, nothing is cut there. Only here, where the hull
            # is known, can the two be told apart; the length rides along in
            # measured_dims because BOM code recomputing from the polygon cannot.
            mouth = piece.exterior.intersection(hull.exterior).length
            out.append(
                Candidate(
                    polygon=piece,
                    kind="notch",
                    shape_fit=min(fit, 1.0),
                    parent_area=host.area,
                    measured_dims={
                        "length_mm": round(length * PT_TO_MM, 2),
                        "width_mm": round(width * PT_TO_MM, 2),
                        "cut_length_mm": round(
                            (piece.exterior.length - mouth) * PT_TO_MM, 2
                        ),
                    },
                    source=source,
                )
            )
    return out


def ideal_obround(mrr: Polygon, length: float, width: float) -> Polygon | None:
    """The stadium that would exactly fill this oriented bounding box.

    A capsule of overall length L and width W is a segment of length (L - W) buffered
    by W/2 along the box's long axis.
    """
    if length <= width:
        return None
    c = list(mrr.exterior.coords)[:4]
    a = LineString([c[0], c[1]]).length
    # midpoints of the two SHORT edges give the long axis
    if a >= LineString([c[1], c[2]]).length:
        m0 = LineString([c[1], c[2]]).interpolate(0.5, normalized=True)
        m1 = LineString([c[3], c[0]]).interpolate(0.5, normalized=True)
    else:
        m0 = LineString([c[0], c[1]]).interpolate(0.5, normalized=True)
        m1 = LineString([c[2], c[3]]).interpolate(0.5, normalized=True)
    axis = LineString([m0, m1])
    if axis.length <= width:
        return None
    spine = LineString(
        [axis.interpolate(width / 2), axis.interpolate(axis.length - width / 2)]
    )
    return spine.buffer(width / 2)


def _shape_fit(poly: Polygon, ideal: Polygon | None) -> float:
    """Intersection-over-union against an ideal shape."""
    if ideal is None or ideal.is_empty:
        return 0.0
    try:
        union = poly.union(ideal).area
        return poly.intersection(ideal).area / union if union else 0.0
    except Exception:
        return 0.0


def _box_fits(poly: Polygon) -> tuple[float, float, float, float] | None:
    """(rect_fit, obround_fit, length_pt, width_pt) against the minimum rotated
    rectangle. Fits are IoU against the ideal shapes themselves, not merely their
    areas: an arbitrary blob can share an obround's area ratio without being one."""
    mrr = poly.minimum_rotated_rectangle
    if not isinstance(mrr, Polygon) or not mrr.area:
        return None
    coords = list(mrr.exterior.coords)
    side_a = LineString(coords[0:2]).length
    side_b = LineString(coords[1:3]).length
    length, width = max(side_a, side_b), min(side_a, side_b)
    rect_fit = poly.area / mrr.area
    obround_fit = _shape_fit(poly, ideal_obround(mrr, length, width))
    return rect_fit, obround_fit, length, width


def _classify(poly: Polygon) -> tuple[str, float, dict]:
    area = poly.area
    perimeter = poly.exterior.length
    circularity = 4 * math.pi * area / perimeter**2 if perimeter else 0.0

    if circularity >= CIRCLE_FIT_THRESHOLD:
        diameter_pt = 2 * math.sqrt(area / math.pi)
        dims = {"diameter_mm": round(diameter_pt * PT_TO_MM, 2)}
        return "hole", circularity, dims

    fits = _box_fits(poly)
    rect_fit = 0.0
    if fits is not None:
        # The old gate (rect_fit >= 0.95) recognised only skinny slots — an obround
        # fills 1 - 0.2146*(W/L) of its box, so a fat one (W/L ~ 0.46) reaches just
        # 0.900 and fell through to "freeform", where the -0.3 penalty auto-rejected it.
        rect_fit, obround_fit, length, width = fits
        fit = max(rect_fit, obround_fit)
        if fit >= SLOT_FIT_THRESHOLD:
            dims = {
                "length_mm": round(length * PT_TO_MM, 2),
                "width_mm": round(width * PT_TO_MM, 2),
            }
            return "slot", min(fit, 1.0), dims

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

    inner = [
        (s, fl) for s, fl in shells if s.area < FRAME_AREA_RATIO * page_area
    ]

    # parent = smallest non-frame shell strictly containing the candidate
    def parent_of(s: Polygon) -> Polygon | None:
        for other, _ in reversed(inner):  # smallest first
            if other.area <= s.area:
                continue
            if other.contains(s.representative_point()):
                return other
        return None

    # A shell with no parent is a part outline, not a cutout. Dedupe runs only among
    # the ones that DO have a parent: a gasket's Ø605 bore overlaps its own Ø686 outline
    # by (605/686)^2 = 78%, so deduping it against the outline deleted the bore — the
    # system was discarding the central hole precisely because the part is a ring.
    # Concentric double-strokes (a hole and its countersink ring) both sit inside the
    # part, both have a parent, and are still deduped as before.
    with_parent = [(s, fl, p) for s, fl in inner if (p := parent_of(s)) is not None]
    kept = _dedupe([(s, fl) for s, fl, _ in with_parent])
    kept_ids = {id(s) for s, _ in kept}

    # A cutout is cut out of a PART. A shape sitting outside every part is on the paper,
    # not in the metal — a title-block symbol, a projection marker, a control frame.
    parts = _part_outlines(inner)

    candidates: list[Candidate] = []
    for s, from_loop, parent in with_parent:
        if id(s) not in kept_ids:
            continue
        if parts and not any(p.contains(s.representative_point()) for p in parts):
            continue
        if s.area > parent.area * MAX_CUTOUT_PARENT_RATIO:
            continue
        kind, shape_fit, dims = _classify(s)
        b = s.bounds
        # contains_text marks an annotation BOX (a title-block cell, a dimension
        # frame) — something whose whole purpose is to hold text. A dimension label
        # sitting inside a large bore is ordinary CAD practice and must not be
        # treated the same way: on ASH-071222 that mistake multiplied the Ø290 bore's
        # score by 0.4 and auto-rejected the only real hole on the sheet. So only
        # flag it when the shape is small enough that the text dominates it.
        text_sized = min(b[2] - b[0], b[3] - b[1]) <= TEXT_BOX_MAX_SPAN_PT
        contains_text = text_sized and any(
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

    # A notch is open to the part's edge, so it is never an enclosed loop or face —
    # it is read off the part outline's concavities instead.
    candidates.extend(_notch_candidates(inner, source))
    return candidates


def extract_candidates(page: fitz.Page) -> list[Candidate]:
    # Only part-geometry ink is polygonized. Leader lines, dimension lines and glyph
    # outlines are annotation and are never candidates — see extraction/ink.py.
    geometry, _annotation = split_ink(page)
    segs = _segments(page, geometry)
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
    tagged = [(p, False) for p in faces] + [
        (p, True) for p in _path_loops(page, geometry)
    ]
    return build_candidates(tagged, abs(page.rect), text_centers)
