"""Ink separation and shape classification — the fixes that took per-drawing recall
from 50% to 92%. Each test names the drawing whose failure motivated it."""

import math

import fitz
import pytest
from shapely.geometry import Point, Polygon

from app.extraction.ink import ANNOTATION, FRAME, GEOMETRY, classify_path, split_ink
from app.extraction.vector import PT_TO_MM, _classify, extract_candidates
from tests.conftest import PDFS_DIR

MM_TO_PT = 1 / PT_TO_MM


def _obround(length_mm: float, width_mm: float) -> Polygon:
    r = width_mm / 2 * MM_TO_PT
    straight = length_mm * MM_TO_PT - 2 * r
    return Point(0, 0).buffer(r).union(Point(straight, 0).buffer(r)).convex_hull


# --- ink classification -----------------------------------------------------


@pytest.mark.parametrize(
    "color,expected",
    [
        ((0.0, 0.0, 0.0), GEOMETRY),  # part edges
        ((0.25, 0.25, 0.25), GEOMETRY),  # A (3) uses this for part lines
        ((0.5, 0.5, 0.5), ANNOTATION),  # ASH dimension + leader lines
        ((0.5, 0.5, 0.0), ANNOTATION),  # 117-626-141_4 dimension lines
        ((0.75, 0.75, 0.75), FRAME),  # sheet border
    ],
)
def test_stroke_colour_says_what_the_ink_is(color, expected):
    assert classify_path({"color": color, "type": "s"}) == expected


def test_fill_only_paths_are_annotation():
    """Dimension arrowheads and solid symbols are filled, never stroked contours."""
    assert classify_path({"color": None, "fill": (0, 0, 0), "type": "f"}) == ANNOTATION


# --- shape classification ---------------------------------------------------


def test_fat_obround_is_a_slot_not_a_freeform():
    """117-626-141_4's two 56x26 slots.

    An obround fills 1 - 0.2146*(W/L) of its bounding box, so a fat one reaches only
    ~0.90. The old gate was rect_fit >= 0.95, which dropped them into "freeform" where
    the -0.3 penalty auto-rejected them: the drawing scored 0% recall.
    """
    kind, fit, dims = _classify(_obround(56.0, 26.0))
    assert kind == "slot"
    assert dims["length_mm"] == pytest.approx(56.0, abs=0.5)
    assert dims["width_mm"] == pytest.approx(26.0, abs=0.5)


def test_skinny_obround_is_still_a_slot():
    """A (3)'s slots — these already worked and must keep working."""
    kind, _, _ = _classify(_obround(62.0, 6.0))
    assert kind == "slot"


def test_rectangle_is_a_slot_kind_with_perfect_fit():
    rect = Polygon(
        [(0, 0), (60 * MM_TO_PT, 0), (60 * MM_TO_PT, 6 * MM_TO_PT), (0, 6 * MM_TO_PT)]
    )
    kind, fit, _ = _classify(rect)
    assert kind == "slot" and fit == pytest.approx(1.0, abs=0.01)


def test_blob_with_an_obrounds_area_ratio_is_still_freeform():
    """Guard against the naive fix: comparing AREA alone let arbitrary faces through.

    Lowering the gate on area ratio alone made Doc_HK3573 report 47 slots instead of 5.
    The fit must be measured against the ideal SHAPE, not merely its area.
    """
    star = Polygon(
        [
            (math.cos(a) * (40 if i % 2 else 100), math.sin(a) * (40 if i % 2 else 100))
            for i, a in enumerate(x * math.pi / 5 for x in range(10))
        ]
    )
    kind, _, _ = _classify(star)
    assert kind == "freeform"


# --- end to end on the real drawings ----------------------------------------


def test_glyph_is_not_extracted_as_a_hole():
    """ASH-071222: the Ø in the label "Ø290 THRU" is drawn as vector paths, and was
    auto-approved at 0.98 as a Ø3.1 hole while the real Ø290 bore was rejected."""
    page = fitz.open(PDFS_DIR / "ASH-071222-TW550-M10_BLANK.pdf")[0]
    cands = extract_candidates(page)

    # the bore, and nothing but the bore
    assert len(cands) == 1
    assert cands[0].kind == "hole"
    # Ø290 on a 1:3.5 sheet -> 82.9mm of paper
    assert cands[0].measured_dims["diameter_mm"] == pytest.approx(82.9, abs=1.0)


def test_a_rings_bore_is_not_deleted_as_a_duplicate_of_the_ring():
    """Doc_HK3573 is a gasket: 16 bolt holes and one central Ø605 bore.

    The bore fills (605/686)^2 = 78% of the Ø686 ring around it, and _dedupe dropped any
    shell overlapping a bigger one by more than 40% — a rule written to kill concentric
    countersink strokes. So the system deleted the central hole *precisely because the
    part is a ring*. Two separate parent-ratio caps then had to be raised to let a bore
    that large survive scoring at all.
    """
    page = fitz.open(PDFS_DIR / "Doc_HK3573_290626083217_00 (1).pdf")[0]
    cands = extract_candidates(page)
    holes = [c for c in cands if c.kind == "hole"]

    # Ø605 on a 1:5 sheet -> 121mm of paper
    bore = [c for c in holes if abs(c.measured_dims["diameter_mm"] - 121.0) < 2.0]
    assert bore, "the central bore of the ring must be found"

    # and the 16 bolt holes: Ø12.5 on a 1:5 sheet -> 2.47mm of paper. (A 2.62mm circle
    # nearby is a known annotation false positive — this sheet is drawn wholly in black,
    # so its layers cannot be separated by colour. It costs a click, not a part.)
    bolts = [c for c in holes if abs(c.measured_dims["diameter_mm"] - 2.47) < 0.1]
    assert len(bolts) == 16


def test_title_block_symbols_are_not_cutouts():
    """A cutout is cut out of the PART. Anything outside every part is on the paper.

    Doc_HK3573's title block holds a "First Angle Projection" symbol (two concentric
    circles) and a ⊕□1 feature-control frame (a square). They are drawn in thick black ink
    and they ARE, geometrically, circles and a square — they scored 0.98 as holes and no
    shape rule will ever say otherwise. WHERE they sit is what makes them not holes.

    A part outline is a big top-level closed loop THAT CONTAINS SOMETHING. Both halves
    matter: without the size test the symbols are top-level loops themselves and admit
    their own innards; without "contains something", 12562 — whose octagonal outline is
    only a planar face, never a loop — declared its own two slots to be parts and threw one
    of them away.
    """
    page = fitz.open(PDFS_DIR / "Doc_HK3573_290626083217_00 (1).pdf")[0]
    cands = extract_candidates(page)

    # 16 bolt holes + the Ø605 bore. Exactly that, and nothing else.
    assert len(cands) == 17, [c.measured_dims for c in cands]
    assert all(c.kind == "hole" for c in cands)


def test_annotation_ink_is_kept_for_the_scale_reader():
    """Annotation paths are filtered out of candidate-building, not thrown away:
    the dimension lines are what the sheet scale is recovered from."""
    page = fitz.open(PDFS_DIR / "ASH-071222-TW550-M10_BLANK.pdf")[0]
    geometry, annotation = split_ink(page)
    assert geometry and annotation


def test_pages_not_following_the_colour_convention_still_extract():
    """Fail safe: a page with no dark ink must not silently extract nothing."""
    doc = fitz.open()
    page = doc.new_page()
    page.draw_circle((100, 100), 20, color=(0.5, 0.5, 0.5))
    page.draw_rect(fitz.Rect(50, 50, 150, 150), color=(0.5, 0.5, 0.5))
    geometry, _ = split_ink(page)
    assert geometry, "all-grey page must fall back to treating non-frame ink as geometry"
