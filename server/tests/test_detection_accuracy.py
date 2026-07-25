"""The accuracy gate: detection may not silently get worse.

`tools/eval_detection.py` has always been the only real measure of whether this pipeline
works, and it was a tool a human had to remember to run. Nothing went red when recall
dropped. Every significant fix in this project was found or validated by that harness and
three were REVERTED by it — which only worked because someone ran it by hand each time.

This runs the same harness, on the same fixtures, as a test. It scores each drawing
against the `expect` block recorded in ground_truth.json, and it is a RATCHET: a drawing
that gets better fails too, demanding the recorded number be raised. A fixture that
quietly says 82% when the pipeline now does 100% is a fixture nobody is reading.

Whole file is ~20s. If that becomes a problem, mark it slow — do not delete it.
"""

import json
from pathlib import Path

import pytest

from tools.eval_detection import (
    PDFS,
    forbidden_hits,
    load_truth,
    open_document,
    score_drawing,
)

TRUTH = load_truth()

# how far the measured score may sit above the recorded one before the fixture is
# considered out of date. Wide enough that one cutout out of 293 does not nag.
RATCHET_SLACK = 0.005

CASES = sorted(name for name in TRUTH if (PDFS / name).exists())


def _expect(spec: dict) -> dict:
    exp = spec.get("expect")
    if exp is None:
        pytest.fail(
            "fixture has no `expect` block — every fixture must state the recall and "
            "precision it is currently held to, or it is not a gate"
        )
    return exp


@pytest.fixture(scope="module")
def scored() -> dict:
    """Score every fixture once; the individual tests read the same results."""
    return {name: score_drawing(name, TRUTH[name], PDFS / name) for name in CASES}


def test_every_fixture_is_present():
    """A fixture PDF that vanishes must fail loudly, not silently shrink the corpus."""
    missing = [n for n in TRUTH if not (PDFS / n).exists()]
    assert not missing, f"ground truth references drawings that are not in pdfs/: {missing}"


@pytest.mark.parametrize("name", CASES, ids=lambda n: n)
def test_recall_does_not_regress(name, scored):
    """Never miss a real hole: a false positive costs a click, a miss costs a part."""
    r = scored[name]
    assert r["scale_known"], f"{name}: no scale in the fixture, cannot score"
    want = _expect(TRUTH[name])["recall"]
    got = r["recall"]
    assert got >= want - 1e-9, (
        f"{name}: recall fell to {got:.1%} from {want:.1%} — "
        f"{len(r['missed'])} true cutout(s) no longer found: "
        f"{sorted({t['shape'] for t in r['missed']})}"
    )
    assert got <= want + RATCHET_SLACK, (
        f"{name}: recall IMPROVED to {got:.1%} (fixture says {want:.1%}). "
        f"Raise `expect.recall` in ground_truth.json — a stale expectation is a gate "
        f"that has stopped guarding anything."
    )


@pytest.mark.parametrize("name", CASES, ids=lambda n: n)
def test_precision_does_not_regress(name, scored):
    r = scored[name]
    want = _expect(TRUTH[name])["precision"]
    got = r["precision"]
    assert got >= want - 1e-9, (
        f"{name}: precision fell to {got:.1%} from {want:.1%} — reported "
        f"{len(r['spurious'])} cutout(s) that match nothing real. First few: "
        f"{[(m['shape'], m['dims'], m['center_pt']) for m in r['spurious'][:3]]}"
    )


@pytest.mark.parametrize("name", CASES, ids=lambda n: n)
def test_nothing_detected_in_a_forbidden_region(name, scored):
    """`must_not_detect` is prose nothing reads; `forbidden_regions` is the same
    statement as coordinates, so it can actually fail.

    Regions come from `tools/export_fixture.py`: a cutout a human rejected during
    review is a confirmed false positive with an exact rectangle.
    """
    spec = TRUTH[name]
    if not spec.get("forbidden_regions"):
        pytest.skip("no forbidden regions recorded for this drawing")
    doc = open_document(PDFS / name)
    try:
        sizes = {i: (doc[i].rect.width, doc[i].rect.height) for i in range(doc.page_count)}
    finally:
        doc.close()
    hits = forbidden_hits(spec, scored[name]["reported"], sizes)
    assert not hits, (
        f"{name}: reported {len(hits)} cutout(s) inside a region confirmed to hold none "
        f"(title block, dimension callout, feature-control frame): "
        f"{[(h['cutout']['shape'], h['cutout']['center_pt']) for h in hits[:3]]}"
    )


@pytest.mark.parametrize("name", [n for n in CASES if TRUTH[n].get("centers_pt")], ids=lambda n: n)
def test_cutouts_are_found_where_they_actually_are(name, scored):
    """Counting shapes is not finding holes.

    The harness matches a reported cutout to a true one by shape and size alone, so
    293 circles of the right diameter in 293 wrong places would score a clean 100%.
    Where a reviewed document has given us exact centres, check them.
    """
    truth_centers = TRUTH[name]["centers_pt"]
    tol_pt = 2.0  # a hole found 2pt from where it is, is the same hole
    for m in scored[name]["reported"]:
        want = truth_centers.get(m["shape"])
        if not want:
            continue
        cx, cy = m["center_pt"]
        near = min(abs(cx - x) + abs(cy - y) for x, y in want)
        assert near <= tol_pt, (
            f"{name}: reported a {m['shape']} at ({cx}, {cy}) — nothing real is within "
            f"{tol_pt}pt of it (closest is {near:.1f}pt away)"
        )


def test_macro_scores_hold(scored):
    """The per-DRAWING average is the honest number: micro is flattered by A (4),
    which contributes 293 identical holes to a corpus of nine drawings."""
    rs = [r for r in scored.values() if r["scale_known"]]
    macro_recall = sum(r["recall"] for r in rs) / len(rs)
    macro_precision = sum(r["precision"] for r in rs) / len(rs)
    want_r = sum(_expect(TRUTH[r["name"]])["recall"] for r in rs) / len(rs)
    want_p = sum(_expect(TRUTH[r["name"]])["precision"] for r in rs) / len(rs)
    assert macro_recall >= want_r - 1e-9, f"macro recall {macro_recall:.1%} < {want_r:.1%}"
    assert macro_precision >= want_p - 1e-9, (
        f"macro precision {macro_precision:.1%} < {want_p:.1%}"
    )
    # precision has been 100% on every drawing since the part-outline gate landed;
    # anything less means the paper-vs-metal gate is leaking again
    assert macro_precision == pytest.approx(1.0), (
        f"macro precision is {macro_precision:.1%} — a cutout is being reported that is "
        f"not in the metal"
    )


def test_ground_truth_records_how_its_sizes_were_obtained():
    """A fixture exported from a reviewed document carries the pipeline's OWN
    measurements. It pins existence and position, never size — a systematic scale
    error would be baked in and the test would go green on it forever. Such entries
    must say so."""
    for name, spec in TRUTH.items():
        assert spec.get("sizes", "confirmed") in {"confirmed", "measured"}, (
            f"{name}: `sizes` must be \"confirmed\" (a human read the drawing) or "
            f"\"measured\" (the pipeline's own numbers)"
        )
