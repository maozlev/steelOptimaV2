"""Regression gate over the synthetic PDF corpus, and the findings it is holding open.

`tools/synth_corpus.py` builds ~910 synthetic drawings across 11 shape families and
scores them against ground truth derived from the declared parameters. Running all of
them takes minutes, so this gate runs the two families that carry live findings in full
plus a deterministic sample of the rest, and asserts THE SET of failing cases has not
grown.

The exact set matters, not the count: a fix that repairs one case and breaks another
would leave the count unchanged and must still fail.
"""

import pytest

from tools.synth.cases import all_cases
from tools.synth_corpus import failures, score_case

# families with live findings run in full; everything else is sampled
FULL_FAMILIES = {"notch", "decoy"}
SAMPLE_STRIDE = 12

# Cases that fail TODAY, each with the finding it represents. Shrinking this set is the
# point; growing it without a note is a regression.
KNOWN_FAILING = {
    # A round-bottomed notch is now FOUND (NOTCH_FIT_THRESHOLD) but a half-disc fits its
    # bounding box at pi/4 = 0.785, which scores 0.6 + 0.25*0.785 + 0.1 = 0.896 — four
    # thousandths under the 0.90 finalize threshold. It can therefore never auto-approve,
    # however clean the drawing. The fix is a half-round ideal shape to fit against, not
    # a lower threshold.
    "notch_semicircle_f0.03_w90.0_colour",
    "notch_semicircle_f0.03_w90.0_width",
    "notch_semicircle_f0.08_w90.0_colour",
    "notch_semicircle_f0.08_w90.0_width",
    # A deep, narrow half-ellipse bite (40pt wide, 165pt deep) is missed outright.
    "notch_semicircle_f0.03_w40.0_colour",
    "notch_semicircle_f0.03_w40.0_width",
    # THE COMMERCIALLY SERIOUS ONE. A second view of the same part — plan plus elevation,
    # which is how essentially every real engineering drawing is laid out — has its hole
    # counted AGAIN, at 0.98 confidence. The BOM then says two holes where the part has
    # one. Nothing in the pipeline knows that two outlines are two VIEWS of one part
    # rather than two parts, and no fixture in the repo covers it.
    "decoy_second_view_colour",
    "decoy_second_view_width",
}


def _cases():
    out = []
    others = []
    for c in all_cases():
        (out if c.family in FULL_FAMILIES else others).append(c)
    return out + others[::SAMPLE_STRIDE]


@pytest.fixture(scope="module")
def failing() -> dict[str, list[str]]:
    bad = {}
    for case in _cases():
        errs = failures(score_case(case))
        if errs:
            bad[case.id] = errs
    return bad


def test_no_new_failures(failing):
    new = sorted(set(failing) - KNOWN_FAILING)
    assert not new, "cases that did not fail before:\n" + "\n".join(
        f"  {i}: {failing[i][0]}" for i in new
    )


def test_known_failures_are_still_failing(failing):
    """A ratchet, like tests/test_detection_accuracy.py. When one of these starts
    passing, delete it from KNOWN_FAILING — a whitelist nobody prunes stops being a
    record of what is broken and becomes a place bugs hide."""
    ran = {c.id for c in _cases()}
    fixed = sorted((KNOWN_FAILING & ran) - set(failing))
    assert not fixed, (
        "these now PASS — remove them from KNOWN_FAILING and note the fix:\n"
        + "\n".join(f"  {i}" for i in fixed)
    )


def test_every_real_cutout_is_detected(failing):
    """Maoz's rule, over the whole sample: a false positive costs a click, a missed
    hole costs a part. Detection — not auto-approval — is the non-negotiable half."""
    missed = {
        cid: [e for e in errs if e.startswith("MISSED")]
        for cid, errs in failing.items()
        if any(e.startswith("MISSED") for e in errs)
    }
    unexpected = sorted(set(missed) - KNOWN_FAILING)
    assert not unexpected, f"real cutouts not detected at all: {unexpected}"
