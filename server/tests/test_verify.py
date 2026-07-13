"""The VLM verification pass — and the guard rails it needs, which were learnt the hard
way by watching it veto a real Ø605 bore with confidence 1.0."""

import pytest
from shapely.geometry import Point

from app.fusion.engine import VETO_CONFIDENCE, fuse_verification
from app.vlm.prompts import VlmVerdict
from app.vlm.verify import group_key
from app.extraction.vector import PT_TO_MM, _classify, Candidate

MM_TO_PT = 1 / PT_TO_MM


def _cand(diameter_mm: float) -> Candidate:
    poly = Point(0, 0).buffer(diameter_mm / 2 * MM_TO_PT, quad_segs=32)
    kind, fit, dims = _classify(poly)
    return Candidate(
        polygon=poly, kind=kind, shape_fit=fit, parent_area=poly.area * 100,
        measured_dims=dims,
    )


def _verdict(is_cutout: bool, confidence: float = 1.0) -> VlmVerdict:
    return VlmVerdict(
        is_cutout=is_cutout,
        kind="hole" if is_cutout else "not_cutout",
        confidence=confidence,
    )


def test_a_veto_flags_but_does_not_erase():
    """This model is not trustworthy enough to hold a delete key.

    Asked about Doc_HK3573 it vetoed the real Ø605 bore at confidence 1.0. A veto must
    therefore leave the cutout plainly visible and one click from restoration, not send it
    to zero. Maoz's rule: a false positive costs a click, a missed hole costs a part.
    """
    out = fuse_verification(0.98, _verdict(is_cutout=False, confidence=1.0))
    assert out == VETO_CONFIDENCE
    assert 0.0 < out < 0.90, "vetoed, but still visible and restorable"


def test_a_confirmation_never_lowers_a_confident_detection():
    """Averaging the model's own confidence in would demote a real hole for the crime of
    the model being only 60% sure of something it got right: 0.5*0.98 + 0.5*0.6 + 0.1 =
    0.89, under the 0.90 finalize threshold. The model objects; it does not grade."""
    assert fuse_verification(0.98, _verdict(True, confidence=0.6)) == 0.98
    assert fuse_verification(0.98, _verdict(True, confidence=1.0)) == 0.98


def test_a_verdict_only_speaks_for_shapes_that_are_really_the_same():
    """The bug that made the model look catastrophically wrong when it was right.

    The BOM groups sizes to 0.5mm so noise does not split one hole type across a dozen
    rows. Reusing that key here swept the title block's "First Angle Projection" symbol
    (Ø2.62mm) into the same bucket as the 16 real Ø2.47mm bolt holes, made the SYMBOL the
    group's representative, and let one correct "not a cutout" veto all 16 real holes.
    """
    symbol = _cand(2.62)
    bolt = _cand(2.47)
    assert group_key(symbol) != group_key(bolt)


def test_identical_holes_share_one_call():
    """A (4) has 293 identical holes. They must cost one question, not 293."""
    keys = {group_key(_cand(9.99)) for _ in range(5)}
    assert len(keys) == 1
