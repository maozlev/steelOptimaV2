"""Sheet scale — the bug that silently cut parts at the wrong size.

Every dimension the extractor measures is in PAPER mm. On a 1:3.5 sheet the gear's Ø290
bore measures 82.9mm of paper, and the BOM reported exactly that.
"""

import fitz
import pytest

from app.extraction.scale import parse_scale_text, resolve_scale
from app.extraction.vector import extract_candidates
from tests.conftest import PDFS_DIR

# (drawing, true scale, the size we must recover)
CASES = [
    ("117-626-141_1_BLANK_Rev.01.pdf", 5.0, 235.0),  # flange, Ø235 THRU
    ("117-626-141_4_Rev.3_BLANK.pdf", 2.0, 56.0),  # plate, 56x26 slots
    ("12562-3000F501023_03.pdf", 0.5, 16.0),  # MAGNIFIED 2:1 sheet
    ("333-532-294_2_BLANK.pdf", 3.0, 75.0),  # washer, Ø75 THRU
    ("ASH-071222-TW550-M10_BLANK.pdf", 3.5, 290.0),  # gear, Ø290 THRU
    ("Doc_HK3573_290626083217_00 (1).pdf", 5.0, 605.0),  # gasket, Ø605 bore
]


def test_a_title_block_scale_is_read_by_position_not_by_regex():
    """Doc_HK3573 prints "Scale 1:5" in its title block, and we could not see it.

    In the PDF's text stream the word "Scale" is token 10 and its value "1:5" is token 99
    — adjacent on the page, nowhere near each other in reading order. A regex over the
    flattened text therefore found nothing, the drawing looked unscaled, and the resolver
    fell back to an unconfident guess of 3.149 for a sheet that says 1:5 in plain sight.
    """
    page = fitz.open(PDFS_DIR / "Doc_HK3573_290626083217_00 (1).pdf")[0]
    assert 5.0 in parse_scale_text(page)


def _resolve(name):
    page = fitz.open(PDFS_DIR / name)[0]
    return page, resolve_scale(page, extract_candidates(page))


@pytest.mark.parametrize("name,scale,_size", CASES, ids=lambda v: str(v)[:24])
def test_sheet_scale_is_recovered(name, scale, _size):
    _page, r = _resolve(name)
    assert r.scale == pytest.approx(scale, rel=0.02)
    assert r.confident, r.note


@pytest.mark.parametrize("name,scale,size", CASES, ids=lambda v: str(v)[:24])
def test_dimensions_come_out_in_real_millimetres(name, scale, size):
    """The number the operator reads must be the number on the drawing."""
    page, r = _resolve(name)
    cands = extract_candidates(page)
    real = []
    for c in cands:
        for v in c.measured_dims.values():
            real.append(v * r.scale)
    assert any(abs(v - size) <= 0.02 * size for v in real), (
        f"expected a {size}mm feature, got {sorted(round(v, 1) for v in real)}"
    )


def test_a_magnified_sheet_scales_down_not_up():
    """12562 is Scale 2:1 — the paper is TWICE real size. Dividing the wrong way would
    double the error instead of fixing it."""
    _page, r = _resolve("12562-3000F501023_03.pdf")
    assert r.scale < 1.0


def test_a_lying_title_block_is_overruled_by_the_drawing():
    """ASH's sheet says "Scale 1:3.5"; its own title block says "SCALE:1:5". The block is
    a stale template default. Believing it would cut every part 43% oversize."""
    page, r = _resolve("ASH-071222-TW550-M10_BLANK.pdf")
    assert 5.0 in parse_scale_text(page)  # the lie is really there
    assert r.scale == pytest.approx(3.5, rel=0.02)
    assert r.confident


def test_an_unverifiable_scale_is_refused_not_guessed():
    """A (3) prints no scale and offers no dimension to check one against. Guessing would
    silently produce wrong parts; the operator is asked instead."""
    _page, r = _resolve("A (3).pdf")
    assert not r.confident


def test_a_page_with_nothing_to_measure_reports_no_scale():
    page = fitz.open().new_page()
    r = resolve_scale(page, [])
    assert r.scale is None and not r.confident and r.note
