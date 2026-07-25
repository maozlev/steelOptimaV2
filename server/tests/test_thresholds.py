"""Probes at each decision point in the pipeline, one constant at a time.

WHY THIS FILE EXISTS
The coloured-geometry bug — a part drawn on a red layer returned ZERO candidates, with
`ink.py`'s own fail-safe unable to help — was found by a twenty-line probe of ONE
decision, in ten minutes. Nine real drawings never found it because none of them happens
to use a coloured layer. A large synthetic corpus would have found it eventually, at
enormously more cost.

So: for every constant the pipeline branches on, drive it directly and pin what happens
on both sides of it. These tests are cheap, they name the constant they guard, and when
one fails it says which decision moved.

They are NOT a substitute for `tests/test_detection_accuracy.py`. A probe proves a rule
behaves as intended on geometry I invented; only a real drawing proves the rule was the
right one. Where the two disagree, the real drawing wins.
"""

import math

import fitz
import pytest
from shapely.geometry import Point, Polygon, box

from app.extraction import scoring, vector
from app.extraction.ink import (
    ANNOTATION,
    FRAME,
    GEOMETRY,
    MAX_FRAME_SATURATION,
    classify_path,
    split_ink,
)
from app.extraction.vector import (
    MIN_CUTOUT_AREA_PT2,
    PT_TO_MM,
    build_candidates,
    extract_candidates,
)

PAGE_AREA = 595.0 * 842.0

# ---------------------------------------------------------------- helpers


def _page(draw, rotation: int = 0) -> fitz.Page:
    """A fresh page with `draw(page)` applied, round-tripped so get_drawings() sees it."""
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    draw(page)
    out = fitz.open("pdf", doc.tobytes())
    p = out[0]
    if rotation:
        p.set_rotation(rotation)
    return p


def _plate_with_holes(color=(0, 0, 0), width=0.7, holes=((180, 200, 20), (320, 200, 20))):
    """A 300x200pt plate containing circular holes: the simplest real drawing there is."""

    def draw(page):
        page.draw_rect(fitz.Rect(100, 100, 400, 300), color=color, width=width)
        for cx, cy, r in holes:
            page.draw_circle(fitz.Point(cx, cy), r, color=color, width=width)

    return _page(draw)


def _holes(page) -> list:
    return [c for c in extract_candidates(page) if c.kind == "hole"]


# ---------------------------------------------------------------- ink: colour


@pytest.mark.parametrize(
    "color",
    [(1, 0, 0), (0, 0, 1), (0, 0.6, 0), (0, 1, 1), (0.5, 0, 0.5)],
    ids=["red", "blue", "green", "cyan", "purple"],
)
def test_a_part_drawn_on_a_coloured_layer_is_not_thrown_away(color):
    """max(r,g,b) measures how LIGHT grey ink is, and pure red is 1.0 — so a red part was
    classified as the sheet border and discarded, returning zero candidates. `ink.py`
    promises the opposite in as many words: "NEVER let this filter reduce a page to
    nothing: a missed hole costs a part."

    Coloured layers are ordinary CAD practice. No sample drawing uses one, which is
    exactly why nine real fixtures scored 100% while this was broken.
    """
    assert len(_holes(_plate_with_holes(color=color))) == 2


def test_olive_dimension_lines_are_still_annotation():
    """The fix must not over-reach. The flange and the plate draw their dimension lines
    in OLIVE (0.5, 0.5, 0.0) — saturated, but mid-grey in lightness, and annotation.
    Treating every saturated stroke as geometry would have broken two real drawings."""
    assert classify_path({"color": (0.5, 0.5, 0.0), "type": "s"}) == ANNOTATION


def test_the_light_grey_sheet_border_is_still_the_frame():
    """Every sample drawing's border is 0.75 grey. That must keep being discarded."""
    assert classify_path({"color": (0.75, 0.75, 0.75), "type": "s"}) == FRAME


@pytest.mark.parametrize("sat,expected", [(0.0, FRAME), (0.1, FRAME), (0.3, ANNOTATION), (1.0, ANNOTATION)])
def test_only_near_grey_ink_can_be_the_frame(sat, expected):
    """Pins MAX_FRAME_SATURATION on both sides: a light GREY is the sheet border, a
    saturated colour of the same lightness is a deliberate layer and is ink."""
    assert sat != MAX_FRAME_SATURATION, "choose test points off the boundary itself"
    assert classify_path({"color": (0.8, 0.8 - sat, 0.8 - sat), "type": "s"}) == expected


def test_fill_only_paths_are_annotation():
    """Arrowheads and solid symbols are never cutout contours."""
    assert classify_path({"color": None, "type": "f"}) == ANNOTATION


def test_one_stray_coloured_stroke_does_not_disable_the_width_fallback():
    """Doc_HK3573 has exactly ONE coloured stroke — a highlight box — and an early
    version of the colour test let it declare the page colour-coded, silently switching
    off width separation. A threshold sweep then gave four identical results and I
    reported that width separation "did not help". It was never switched on."""

    def draw(page):
        page.draw_rect(fitz.Rect(100, 100, 400, 300), color=(0, 0, 0), width=1.4)
        page.draw_circle(fitz.Point(250, 200), 20, color=(0, 0, 0), width=1.4)
        for i in range(20):  # thin black dimension lines
            page.draw_line((110, 320 + i), (390, 320 + i), color=(0, 0, 0), width=0.35)
        page.draw_rect(fitz.Rect(60, 60, 90, 90), color=(0.5, 0.5, 0.0), width=0.5)

    geometry, annotation = split_ink(_page(draw))
    assert annotation, "the thin dimension lines must be separated out by WIDTH"
    assert all((p.get("width") or 0) >= 0.4 for p in geometry)


def test_a_page_following_no_convention_is_never_left_blind():
    """One colour, one width: neither convention applies. Treat everything as geometry —
    noisier, but a missed hole costs a part and a false positive costs a click."""
    page = _plate_with_holes(color=(0, 0, 0), width=0.7)
    geometry, _ = split_ink(page)
    assert len(geometry) == 3
    assert len(_holes(page)) == 2


def test_the_ink_convention_chosen_per_drawing_is_pinned(request):
    """`test_ink.py` tests how a single stroke is classified. The decision that actually
    matters is the PAGE-level one, and it had no test at all — which is how the bug
    above survived. Half the sample drawings take each path."""
    pdfs = request.config.rootpath.parent / "pdfs"
    expected = {
        "117-626-141_1_BLANK_Rev.01.pdf": "colour",
        "117-626-141_4_Rev.3_BLANK.pdf": "colour",
        "12562-3000F501023_03.pdf": "colour",
        "ASH-071222-TW550-M10_BLANK.pdf": "colour",
        "333-532-294_2_BLANK.pdf": "width",
        "A (3).pdf": "width",
        "A (4).pdf": "width",
        "Doc_HK3573_290626083217_00 (1).pdf": "width",
    }
    for name, want in expected.items():
        path = pdfs / name
        if not path.exists():
            pytest.skip(f"{name} not present")
        with fitz.open(path) as doc:
            page = doc[0]
            paths = page.get_drawings()
            strokes = [p for p in paths if p.get("color") is not None]
            coloured = [p for p in strokes if classify_path(p) == ANNOTATION]
            got = "colour" if len(coloured) >= 0.05 * len(strokes) else "width"
        assert got == want, f"{name}: reads as {got}-coded, fixture says {want}"


# ---------------------------------------------------------------- size limits


@pytest.mark.parametrize("r_pt", [0.6, 0.8, 1.0, 1.2, 2.0, 5.0, 20.0])
def test_smallest_findable_hole(r_pt):
    """MIN_CUTOUT_AREA_PT2 = 4.0 is a floor in PAPER area, so what it means in real
    millimetres depends entirely on the sheet scale. A circle needs r >= 1.13pt to
    clear it — 0.4mm of paper, which on a 1:5 sheet is a real 4mm hole.

    This pins where the floor actually bites, in both directions. Steel drawings do
    carry 4mm holes; if a customer's sheet is plotted small, they vanish silently.
    """
    found = len(_holes(_plate_with_holes(holes=((250, 200, r_pt),))))
    clears_floor = math.pi * r_pt**2 >= MIN_CUTOUT_AREA_PT2
    assert bool(found) == clears_floor, (
        f"r={r_pt}pt (area {math.pi * r_pt**2:.1f}pt^2) "
        f"{'should' if clears_floor else 'should not'} be found"
    )


@pytest.mark.parametrize("frac", [0.30, 0.50, 0.70, 0.78, 0.85, 0.92, 0.97])
def test_how_much_of_its_part_a_hole_may_fill(frac):
    """A gasket's bore fills 78% of its ring. TWO separate caps independently rejected
    it for being "too big for its part", and the system deleted the central hole
    precisely because the part is a ring.

    Above the cap a "hole" is a double-stroked outline, not a cutout.
    """
    part_r = 100.0
    hole_r = part_r * math.sqrt(frac)
    shells = [
        (Point(300, 400).buffer(part_r, quad_segs=64), True),
        (Point(300, 400).buffer(hole_r, quad_segs=64), True),
    ]
    cands = build_candidates(shells, PAGE_AREA, [])
    holes = [c for c in cands if c.kind == "hole"]
    assert bool(holes) == (frac <= vector.MAX_CUTOUT_PARENT_RATIO), (
        f"a bore filling {frac:.0%} of its part was "
        f"{'dropped' if not holes else 'kept'}"
    )


def test_the_two_parent_ratio_caps_track_each_other():
    """`scoring.py` says in a comment that these must track. Nothing enforced it, and
    each one independently rejected the gasket's real Ø605 bore. Cheapest test here."""
    assert vector.MAX_CUTOUT_PARENT_RATIO == scoring.MAX_PARENT_RATIO


@pytest.mark.parametrize("frac", [0.005, 0.02, 0.05, 0.14])
def test_how_big_a_bite_has_to_be_before_it_is_a_notch(frac):
    """NOTCH_MIN_HOST_FRAC separates a manufactured cut from the part's own shape: a
    gear tooth gap is ~0.2% of the gear, the flange's real notch is 14% of its part.

    The notch detector has exactly ONE real positive example in the whole repo. This is
    the only place its size gate is exercised on both sides.
    """
    w, h = 200.0, 100.0
    notch_w = 40.0
    notch_d = frac * w * h / notch_w  # area = frac of the uncut plate
    plate = Polygon(
        [
            (0, 0), (w, 0), (w, h),
            (120, h), (120, h - notch_d), (120 - notch_w, h - notch_d), (120 - notch_w, h),
            (0, h),
        ]
    )
    shells = [(plate, False), (Point(40, 40).buffer(10, quad_segs=32), True)]
    notches = [c for c in build_candidates(shells, PAGE_AREA, []) if c.kind == "notch"]
    assert bool(notches) == (frac >= vector.NOTCH_MIN_HOST_FRAC), (
        f"a notch covering {frac:.1%} of its part was "
        f"{'kept' if notches else 'dropped'}"
    )


@pytest.mark.parametrize("frac", [0.01, 0.05, 0.30])
def test_a_small_part_beside_a_big_one_still_counts_as_a_part(frac):
    """MIN_PART_AREA_FRAC exists to stop a title-block symbol declaring itself a part
    and admitting its own insides. But a sheet with a big bracket and a small washer on
    it is ordinary — and the washer's holes must survive."""
    big = box(50, 50, 450, 450)
    side = math.sqrt(big.area * frac)
    small = box(480, 50, 480 + side, 50 + side)
    shells = [
        (big, True),
        (Point(250, 250).buffer(30, quad_segs=32), True),
        (small, True),
        (Point(480 + side / 2, 50 + side / 2).buffer(side / 6, quad_segs=32), True),
    ]
    cands = build_candidates(shells, PAGE_AREA, [])
    centres = [c.polygon.centroid.x for c in cands if c.kind == "hole"]
    found_small = any(cx > 470 for cx in centres)
    assert found_small == (frac >= vector.MIN_PART_AREA_FRAC), (
        f"the small part is {frac:.0%} of the big one; its hole was "
        f"{'found' if found_small else 'lost'}"
    )


# ---------------------------------------------------------------- geometry recovery


def _almost_closed_ring(gap_pt: float, single_path: bool):
    """A 48-segment ring left `gap_pt` short of closing."""

    def draw(page):
        page.draw_rect(fitz.Rect(100, 100, 400, 300), color=(0, 0, 0), width=0.7)
        n, r, cx, cy = 48, 25.0, 250.0, 200.0
        span = 2 * math.pi - (gap_pt / r)
        pts = [
            (cx + r * math.cos(span * i / n), cy + r * math.sin(span * i / n))
            for i in range(n + 1)
        ]
        if single_path:
            page.draw_polyline(pts, color=(0, 0, 0), width=0.7)
        else:
            for a, b in zip(pts, pts[1:]):
                page.draw_line(a, b, color=(0, 0, 0), width=0.7)

    return _page(draw)


@pytest.mark.parametrize("gap_pt", [0.0, 0.5, 1.0, 1.4, 1.6, 3.0, 8.0])
def test_a_contour_that_does_not_quite_close(gap_pt):
    """CAD exports often draw a contour as one polyline that does not numerically close;
    shapely's polygonize silently drops such rings. LOOP_CLOSE_TOL_PT force-closes small
    gaps — this pins the boundary from both sides."""
    found = len(_holes(_almost_closed_ring(gap_pt, single_path=True)))
    assert bool(found) == (gap_pt <= vector.LOOP_CLOSE_TOL_PT), (
        f"a {gap_pt}pt gap was {'lost' if not found else 'recovered'}, but the "
        f"tolerance is {vector.LOOP_CLOSE_TOL_PT}pt"
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "KNOWN LIMIT, found by this file. The gap-closing above chains items WITHIN one "
        "path; a contour emitted as separate per-segment paths never reaches it. Such a "
        "ring is recovered only when it closes EXACTLY (polygonize forms a planar face) "
        "— at any gap >= 0.5pt the hole is silently lost. Narrow trigger, but a whole "
        "hole when it fires. The fix is to snap segment endpoints before polygonize, "
        "which is a real change to the planar arrangement: run tools/eval_detection.py "
        "before and after. When it lands, this xfail turns red — delete it then."
    ),
)
@pytest.mark.parametrize("gap_pt", [0.5, 1.0, 1.4])
def test_a_split_contour_that_does_not_quite_close(gap_pt):
    assert len(_holes(_almost_closed_ring(gap_pt, single_path=False))) == 1


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_page_rotation_changes_nothing(rotation):
    """`page.get_drawings()` returns UNROTATED coordinates while renders and text are in
    rotated space — a documented landmine with no test until now. A rotated sheet is the
    same part."""
    page = _page(
        lambda p: (
            p.draw_rect(fitz.Rect(100, 100, 400, 300), color=(0, 0, 0), width=0.7),
            p.draw_circle(fitz.Point(180, 200), 20, color=(0, 0, 0), width=0.7),
            p.draw_circle(fitz.Point(320, 200), 12, color=(0, 0, 0), width=0.7),
        ),
        rotation=rotation,
    )
    holes = _holes(page)
    assert len(holes) == 2
    dias = sorted(round(h.measured_dims["diameter_mm"], 1) for h in holes)
    assert dias == pytest.approx([24 * PT_TO_MM, 40 * PT_TO_MM], abs=0.3)


CIRCLE_R_PT = 30.0
TRUE_CIRCUMFERENCE_MM = 2 * math.pi * CIRCLE_R_PT * PT_TO_MM


def _polygonised_circle(n_sides: int):
    from app.bom.shapes import shape_metrics

    poly = Point(0, 0).buffer(CIRCLE_R_PT, quad_segs=max(1, n_sides // 4))
    m = shape_metrics(poly)
    if m["shape"] != "circle":
        pytest.skip(f"a {n_sides}-gon does not read as a circle; not what this pins")
    return poly, m


@pytest.mark.parametrize("n_sides", [8, 16, 32, 64])
def test_cut_length_is_pi_d_and_not_the_polygon_perimeter(n_sides):
    """A CAD circle is a many-segment polyline and a snapped raster circle is a 16-gon,
    so cut length is computed from the ideal shape rather than the polygon."""
    _, m = _polygonised_circle(n_sides)
    # both are rounded to 2dp for display; pi magnifies the diameter's rounding to ~0.016
    assert m["cut_length_mm"] == pytest.approx(math.pi * m["dims"]["diameter_mm"], abs=0.02)


@pytest.mark.xfail(
    strict=True,
    reason=(
        "FOUND BY THIS FILE, and it contradicts the stated rationale for pi*d. The "
        "diameter is derived from the polygon's AREA (d = 2*sqrt(A/pi)), and an "
        "inscribed n-gon has LESS area than its circle — so pi*d inherits the "
        "under-measurement instead of curing it, and is in fact slightly WORSE than the "
        "polygon perimeter for fine polygons. Cut length and reported diameter both read "
        "low: -5.1% at 8 sides, -1.3% at 16 (a snapped raster circle), -0.33% at 32, "
        "-0.1% at 64. Real CAD circles are fine polygons so this is small, but it is a "
        "one-directional bias in the number that is quoted and cut, and raster is where "
        "it bites. The fix is to take the diameter from the circumradius rather than the "
        "area, which moves EVERY measured size — run tools/eval_detection.py before and "
        "after, and expect the raster size-bias fixture tolerance to change with it."
    ),
)
@pytest.mark.parametrize("n_sides", [8, 16])
def test_a_coarse_polygon_measures_the_circle_it_stands_for(n_sides):
    _, m = _polygonised_circle(n_sides)
    assert m["cut_length_mm"] == pytest.approx(TRUE_CIRCUMFERENCE_MM, rel=0.005)


def test_an_exact_restroke_is_deduped_but_a_nested_bore_is_not():
    """Overlap is not duplication. A gasket's bore fills 78% of its ring; the old rule
    dropped anything overlapping by >40% IoU and deleted the central hole."""
    ring = Point(300, 400).buffer(100, quad_segs=64)
    bore = Point(300, 400).buffer(88, quad_segs=64)
    restroke = Point(300, 400).buffer(100.2, quad_segs=64)
    kept = vector._dedupe(
        sorted([(ring, True), (bore, True), (restroke, True)], key=lambda s: -s[0].area)
    )
    assert len(kept) == 2, "the restroke should go, the nested bore should stay"
    assert min(p.area for p, _ in kept) == pytest.approx(bore.area, rel=1e-6)


def test_a_shape_outside_every_part_is_not_a_cutout():
    """A cutout is cut out of the PART. The title block's projection symbol is two
    concentric circles that score 0.98 as a hole; what makes it not one is WHERE it is."""
    shells = [
        (box(100, 100, 400, 300), True),
        (Point(250, 200).buffer(20, quad_segs=32), True),  # a real hole, in the metal
        (Point(520, 700).buffer(8, quad_segs=32), True),  # on the paper, in the title block
    ]
    cands = build_candidates(shells, PAGE_AREA, [])
    assert [c.polygon.centroid.y for c in cands if c.kind == "hole"] == pytest.approx([200], abs=1)
