import math

import pytest
from shapely.geometry import Point, Polygon

from app.bom.service import build_rows, totals
from app.bom.shapes import shape_metrics
from app.extraction.vector import PT_TO_MM

MM_TO_PT = 1 / PT_TO_MM


def circle(diameter_mm: float, segments: int = 64) -> Polygon:
    return Point(0, 0).buffer(diameter_mm / 2 * MM_TO_PT, quad_segs=segments // 4)


def rectangle(length_mm: float, width_mm: float) -> Polygon:
    length, width = length_mm * MM_TO_PT, width_mm * MM_TO_PT
    return Polygon([(0, 0), (length, 0), (length, width), (0, width)])


def obround(length_mm: float, width_mm: float) -> Polygon:
    """Stadium: a width_mm-wide capsule of overall length length_mm."""
    r = width_mm / 2 * MM_TO_PT
    straight = (length_mm * MM_TO_PT) - 2 * r
    return Point(0, 0).buffer(r).union(Point(straight, 0).buffer(r)).convex_hull


class FakeCutout:
    """Stands in for a Cutout row: build_rows only touches these fields."""

    def __init__(self, poly, cid=1, status="approved", kind="hole", page_id=1):
        self.id = cid
        self.page_id = page_id
        self.status = status
        self.kind = kind
        self.geometry_wkt = poly.wkt
        self.edited_geometry_wkt = None


# --- shape classification ---------------------------------------------------


def test_circle_is_a_circle_and_cuts_pi_d():
    m = shape_metrics(circle(10.0))
    assert m["shape"] == "circle"
    assert m["dims"]["diameter_mm"] == pytest.approx(10.0, abs=0.05)
    assert m["cut_length_mm"] == pytest.approx(math.pi * 10.0, rel=0.01)


def test_rectangle_is_not_reported_as_a_slot():
    """The DB kind enum calls both of these "slot"; the BOM must not."""
    m = shape_metrics(rectangle(60.0, 6.0))
    assert m["shape"] == "rectangle"
    assert m["cut_length_mm"] == pytest.approx(2 * (60.0 + 6.0), rel=0.01)


def test_obround_is_reported_as_a_slot():
    m = shape_metrics(obround(60.0, 6.0))
    assert m["shape"] == "slot"
    # two straights plus the two semicircular ends
    assert m["cut_length_mm"] == pytest.approx(2 * (60.0 - 6.0) + math.pi * 6.0, rel=0.02)


def test_rectangle_and_obround_of_equal_size_have_different_cut_lengths():
    rect = shape_metrics(rectangle(60.0, 6.0))["cut_length_mm"]
    slot = shape_metrics(obround(60.0, 6.0))["cut_length_mm"]
    assert rect != slot


def test_notch_kind_is_preserved_over_geometry():
    m = shape_metrics(rectangle(20.0, 10.0), kind="notch")
    assert m["shape"] == "notch"


def test_irregular_falls_back_to_polygon_perimeter():
    poly = Polygon([(0, 0), (100, 0), (90, 40), (60, 10), (0, 50)])
    m = shape_metrics(poly)
    assert m["shape"] == "irregular"
    assert m["cut_length_mm"] == pytest.approx(poly.exterior.length * PT_TO_MM, rel=0.001)


# --- grouping ---------------------------------------------------------------


def test_rows_group_by_shape_and_size_and_sum_cut_length():
    cutouts = [FakeCutout(circle(5.0), cid=i) for i in range(10)]
    rows = build_rows(cutouts)
    assert len(rows) == 1
    assert rows[0]["qty"] == 10
    assert rows[0]["cut_length_total_mm"] == pytest.approx(
        10 * rows[0]["cut_length_each_mm"], rel=0.01
    )


def test_displayed_dims_reconcile_with_cut_length():
    """A row labelled 'O 5.2 mm' must have a cut length of pi x 5.2 — if the label
    is rounded but the length is not, the operator stops trusting the table."""
    rows = build_rows([FakeCutout(circle(5.163), cid=i) for i in range(4)])
    shown = float(rows[0]["dims"].replace("Ø", "").replace("mm", "").strip())
    assert rows[0]["cut_length_each_mm"] == pytest.approx(math.pi * shown, rel=0.01)


def test_near_identical_sizes_collapse_into_one_row():
    cutouts = [
        FakeCutout(circle(5.0), cid=1),
        FakeCutout(circle(5.1), cid=2),
        FakeCutout(circle(4.9), cid=3),
    ]
    assert len(build_rows(cutouts)) == 1


def test_distinct_sizes_stay_separate():
    cutouts = [FakeCutout(circle(5.0), cid=1), FakeCutout(circle(12.0), cid=2)]
    assert len(build_rows(cutouts)) == 2


def test_rejected_cutouts_carry_no_quantity_or_cut_length():
    cutouts = [
        FakeCutout(circle(5.0), cid=1, status="approved"),
        FakeCutout(circle(5.0), cid=2, status="rejected"),
    ]
    rows = build_rows(cutouts)
    assert rows[0]["qty"] == 1
    assert rows[0]["rejected_ids"] == [2]
    assert rows[0]["cut_length_each_mm"] == pytest.approx(
        rows[0]["cut_length_total_mm"], rel=0.001
    )


def test_pending_quantity_is_tracked_separately():
    cutouts = [
        FakeCutout(circle(5.0), cid=1, status="approved"),
        FakeCutout(circle(5.0), cid=2, status="pending"),
    ]
    rows = build_rows(cutouts)
    assert rows[0]["qty"] == 2 and rows[0]["pending_qty"] == 1


def test_the_sheet_scale_reaches_the_bom():
    """A Ø47 circle of ink on a 1:5 sheet is a Ø235 hole in the steel.

    Without this the gear's Ø290 bore was reported as Ø82.9 and its cut length was 3.5x
    short — numbers that would be cut, not merely displayed.
    """
    unscaled = build_rows([FakeCutout(circle(47.0), page_id=7)])
    scaled = build_rows([FakeCutout(circle(47.0), page_id=7)], scales={7: 5.0})

    assert scaled[0]["cut_length_each_mm"] == pytest.approx(
        5 * unscaled[0]["cut_length_each_mm"], rel=0.001
    )
    assert scaled[0]["cut_length_each_mm"] == pytest.approx(math.pi * 235.0, rel=0.02)


def test_a_magnified_sheet_scales_dimensions_down():
    """12562 is Scale 2:1: 32mm of ink is a 16mm slot. Dividing the wrong way here would
    double the error instead of fixing it."""
    rows = build_rows([FakeCutout(circle(32.0), page_id=3)], scales={3: 0.5})
    assert rows[0]["cut_length_each_mm"] == pytest.approx(math.pi * 16.0, rel=0.02)


def test_no_scale_leaves_paper_dimensions_untouched():
    """A page whose scale could not be established must not be silently multiplied by
    anything — the numbers stay as measured and the API flags them as unverified."""
    rows = build_rows([FakeCutout(circle(10.0), page_id=1)], scales={1: None})
    assert rows[0]["cut_length_each_mm"] == pytest.approx(math.pi * 10.0, rel=0.02)


def test_totals_sum_every_row():
    cutouts = [FakeCutout(circle(5.0), cid=1), FakeCutout(rectangle(60, 6), cid=2)]
    rows = build_rows(cutouts)
    t = totals(rows)
    assert t["qty"] == 2
    assert t["cut_length_mm"] == pytest.approx(
        sum(r["cut_length_total_mm"] for r in rows), rel=0.001
    )
