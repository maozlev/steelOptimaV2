"""Annotation ink drawn THROUGH a hole on a scan — backlog item 2, as a test.

A5.png finds 13 of 16 bolt holes because a leader arrow and a dimension arc cross three
of them. `tools/eval_ink_crossing.py` reproduces that mechanism with the cause on a dial;
this file pins the two ends of what that sweep found, so the fix announces itself:

  * a big hole survives being crossed, always — that must not regress;
  * a bolt-hole-sized one does not, and today ~10% of crossings destroy it outright.

The sweep's most useful result is that the failure is NOT monotonic in ink width, angle
or offset. It is an aliasing threshold, so a fix cannot be tuned against a few cases —
which is exactly what fixing it against A5.png's three examples would have been.
"""

import tempfile
from pathlib import Path

import pytest

from tools.eval_ink_crossing import BOLT_HOLE_PT, hole_found

DPI = 300


@pytest.fixture(scope="module")
def out_dir():
    return Path(tempfile.mkdtemp(prefix="inkcross_test_"))


def test_an_uncrossed_hole_is_found(out_dir):
    """The baseline. Without this, every result below means nothing."""
    assert hole_found(BOLT_HOLE_PT, None, 0, 0, DPI, out_dir, "base_small")


@pytest.mark.parametrize("angle", [0, 45, 90])
@pytest.mark.parametrize("offset", [0.0, 0.8])
def test_a_large_hole_survives_being_crossed(out_dir, angle, offset):
    """A 20pt hole is crossed by the same ink and keeps enough interior to re-fuse —
    the synthetic counterpart of A5.png's Ø605 bore, which survives while its Ø12.5
    bolt holes do not. A fix for the small case must not cost this one."""
    assert hole_found(
        20.0, angle, offset, 1.4, DPI, out_dir, f"big_{angle}_{offset}"
    ), "a large hole lost its interior to a single crossing line"


@pytest.mark.xfail(
    strict=True,
    reason=(
        "BACKLOG ITEM 2, reproduced synthetically. A 0.7pt line crossing the centre of a "
        "bolt-hole-sized hole at 90 degrees shatters its interior into fragments below "
        "the sliver re-fusion floor, and the hole is dropped from the BOM entirely. "
        "Reproduces at 150 and 300 DPI. When ink-crossed recovery lands this turns red — "
        "delete the marker, then re-run tools/eval_ink_crossing.py for the full surface "
        "and tools/eval_detection.py to confirm A5.png moved above 13/16."
    ),
)
def test_a_bolt_hole_survives_being_crossed(out_dir):
    assert hole_found(BOLT_HOLE_PT, 90, 0.0, 0.7, DPI, out_dir, "small_90_0.0")
