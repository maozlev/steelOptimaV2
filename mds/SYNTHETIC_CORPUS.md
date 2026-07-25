# The synthetic PDF corpus — what it is and what it found

Built 2026-07-26. ~910 generated PDF drawings, 1,710 cutouts, 11 shape families, both ink
conventions. Runs in about four minutes.

```bash
uv run python tools/synth_corpus.py                      # build + score everything
uv run python tools/synth_corpus.py --family notch slot  # one family
uv run python tools/synth_corpus.py --failures           # every failing case, itemised
uv run python tools/synth_corpus.py --keep ../synth_out  # write the PDFs to look at
```

Gate: `tests/test_synth_corpus.py` (3.5 s) — runs the families with live findings in full
plus a deterministic sample of the rest, and asserts the SET of failing cases has not grown.
It ratchets in both directions: a case that starts passing must be removed from
`KNOWN_FAILING`, or the whitelist stops being a record of what is broken.

---

## Design

**Ground truth comes from the declared parameters**, not from reading the drawn geometry
back. A circle case says "d = 16pt at (297, 355)"; the drawer renders it and the scorer
checks that, independently. If both sides read the polygon, a broken emitter would produce
matching-but-wrong truth and the case would pass on a garbage drawing.

**Two questions are scored separately, and conflating them would invent failures:**

- `detect` — a candidate exists at all. This is "never miss a real hole" and it is asserted
  for every real opening.
- `approve` — it clears the 0.90 finalize threshold and enters the BOM unattended. Asserted
  only for shapes with an ideal fit. A freeform cutout carries a −0.3 penalty *by design*
  and is meant to be surfaced for review; scoring that as a miss would manufacture ~100
  fake failures. It shows as `approve 0%` for the freeform family, and that is correct.

**Matching is positional.** A reported cutout counts only if its centroid is within
tolerance of where the shape was actually drawn — counting shapes is not finding holes.

---

## What it found

The first full run had **408 failures. 330 of them were the scorer, not the pipeline** —
compared against axis-aligned bounding boxes (wrong for rotated shapes), expected holes on
seven decoy pages that never drew one, and a self-intersecting keyhole. Worth stating plainly:
**most of a new corpus's first output is the corpus being wrong.** Triage before reporting.

The 78 that survived triage were real. Four findings:

### 1. A squat slot was reported as a circle — FIXED

An obround of aspect 1.5 has circularity 0.94, clearing `CIRCLE_FIT_THRESHOLD` (0.90), and
`shape_metrics` returned on that without ever comparing the obround fit. A 30×20 adjustment
slot came back as a Ø27.6 circle: wrong shape, wrong tooling, wrong cut length, and a size
nobody can make.

**Fix:** a circle must also be square in its minimum rotated rectangle
(`CIRCLE_MAX_ASPECT = 1.15`). 36 failing cases → 0, real drawings unchanged.

### 2. Round-bottomed notches were invisible — FIXED

The notch gate reused `SLOT_FIT_THRESHOLD` (0.90), a number chosen for *enclosed* cutouts.
Measured over the notch family:

| bite | shape fit | was |
|---|---|---|
| rectangular | 1.000 | found |
| stepped | 0.875 | **rejected** |
| semicircular | **0.785** | **rejected** |
| round-ended slot | 0.761 | **rejected** |
| gear tooth gap | ~0.60 | correctly rejected |
| V bite / chamfer | 0.503 | correctly rejected |
| tapered beam end (A (3)) | 0.500 | correctly rejected |

No tuning of the *rectangle* fit could ever have fixed this: a half-disc fills exactly π/4 =
0.785 of its bounding box by construction. Coped, radiused and drainage notches are standard
steel practice and the detector could not see any of them.

Real cuts and part-shape separate by 0.16, so `NOTCH_FIT_THRESHOLD = 0.70` sits in the middle
of the gap rather than on either edge. Notch detection 75% → 98%, real drawings unchanged.

This is exactly the blind spot `mds/HANDOFF.md` predicted: *"the detector has exactly ONE real
positive example… nothing has yet tested a small real notch, a semicircular one."*

### 3. A round notch can be found but can never auto-approve — OPEN

With fit 0.785, the score is `0.6 + 0.25 × 0.785 + 0.1 = 0.896` — **four thousandths under
the 0.90 finalize threshold.** However clean the drawing, a semicircular notch always needs
a click. The fix is a half-round ideal shape to fit against (which would score it ~0.95), not
a lower threshold. Held by `KNOWN_FAILING`.

### 4. A second view double-counts every hole — OPEN, and the serious one

A sheet showing the part in plan *and* elevation has the elevation's hole reported again, at
**0.98 confidence**. The BOM then says two holes where the part has one.

Nothing in the pipeline distinguishes "two views of one part" from "two parts", and **no
fixture in the repo covers it** — yet essentially every real engineering drawing is laid out
this way. On a three-view sheet the quantity inflation is 3×. This is a quoting and
manufacturing error, not a cosmetic one.

It is the strongest argument for the real-drawing work in `mds/ROAD_TO_100.md`: the corpus can
show the failure exists, but only real multi-view sheets can show how often it fires and what
distinguishes a view from a part.

---

## Current state

```
family        cases  fail  cutouts  detect  approve  shape   size
adversarial      30     0       36    100%     100%   100%   100%
circle           60     0       60    100%     100%   100%   100%
decoy            28     2       28    100%     100%   100%   100%
freeform         96     0       96    100%       0%   100%    75%   <- approval not asserted
nested           32     0       24    100%     100%   100%   100%
notch            70     6       98     98%      94%    98%    98%
pattern          48     0      822    100%     100%   100%   100%
polygon          84     0       84    100%      71%   100%   100%   <- 3/5-gons: detect only
rectangle       240     0      240    100%     100%   100%   100%
sheet_size       18     0       18    100%     100%   100%   100%
slot            204     0      204    100%     100%   100%   100%
TOTAL           910     8     1710    100%      93%   100%    98%
```

**Every real cutout in the corpus is now detected.** The 8 open cases are findings 3 and 4.

---

## What this does not prove

These drawings encode the drafting conventions I know about. They cannot contain the one that
breaks the pipeline next — the coloured-layer bug scored 100% on nine real drawings right up
until somebody probed that one decision directly.

910 green cases means **regressions will be caught**. It does not mean the next customer sheet
is read correctly. The evidence for that is still real labelled drawings, and
`tools/export_fixture.py` makes each review produce one.
