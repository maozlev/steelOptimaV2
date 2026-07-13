# Detection accuracy — plan

Goal: correctly find every manufacturing cutout on a drawing, with correct real-world
dimensions, and reject everything that is not a cutout.

## The honest ceiling

**100% on arbitrary PDFs is not achievable, and today it is not even measurable.**
There is no ground truth in this repo — nothing states that the gear drawing has exactly
one Ø290 bore. Until that exists, "100% accuracy" is a feeling, not a number. Phase A
therefore comes first and is not optional.

The drawings themselves name the real 100% route ("Part to be done according to file DXF
attached"): a DXF carries exact geometry, layers and units, and needs no CV at all. We are
PDF-only by constraint, so we are reconstructing what the DXF already states. Expect to
reach the high 90s and to know precisely where we sit — not to reach certainty.

## What is actually broken (measured 2026-07-13, not guessed)

Evidence from the seven sample drawings:

1. **Every dimension is in paper millimetres, not real millimetres.** The gear's Ø290 bore
   is reported as Ø82.9 because the sheet is 1:3.5. The flange's Ø235 reads Ø47.0 at 1:5.
   The washer's Ø75 reads Ø25.0 at 1:3. Every BOM size and every cut length is wrong by the
   scale factor. A (3) and A (4) only looked right because nothing cross-checked them.
   *This is the most dangerous bug in the system: it silently produces wrong parts.*

2. **A typographic glyph is approved as a hole.** On ASH, the `Ø` character in the label
   "Ø290 THRU" is drawn as vector paths (it is not in `get_text`), gets polygonized, and
   scores **0.98 — auto-approved — as a Ø3.1 mm hole.**

3. **The only real hole on that drawing is rejected.** The Ø290 bore scores **0.400**: the
   leader text sits inside it, and `TEXT_PENALTY_FACTOR` multiplies the score by 0.4. A
   label inside a large bore is normal CAD practice, not evidence of an annotation box.

4. **Stubby obround slots are missed entirely.** `RECT_FIT_THRESHOLD = 0.95`, but an obround
   fills only `1 - 0.2146·(W/L)` of its bounding box. The plate's two 56×26 slots land at
   rect_fit 0.900 → classified `freeform` → `FREEFORM_PENALTY` −0.3 → 0.675 and 0.625, both
   below the 0.90 finalize threshold. Thin slots (A (3), W/L≈0.1 → 0.979) pass; fat ones do
   not. The classifier only recognises slots that happen to be skinny.

5. **Leader lines and dimension lines enclose fake faces.** The arrows from "Ø325" and
   "Ø75" cross the circle and bound triangular regions that polygonize into candidates.
   Currently they score low enough to be dropped, but only by luck of the shape heuristics.

6. **Title blocks lie about scale.** ASH: sheet says `Scale 1:3.5`, title block says
   `SCALE:1:5`. The plate: sheet `Scale 1:2`, block `SCALE:1:1`. The block is a stale
   template default. Never trust it alone.

## Root cause

The extractor polygonizes **all ink on the page** — part edges, leader lines, arrowheads,
centrelines and glyph outlines — into one soup, then tries to sort cutouts back out with
shape heuristics downstream. Shape alone cannot distinguish a Ø glyph from a small hole,
because they are the same shape.

But the CAD exports already separate these by **stroke colour**, and nothing reads it:

| role | ASH | 117-626-141_4 |
|---|---|---|
| part geometry | black `(0,0,0)` | black `(0,0,0)` |
| dimension / leader | grey `(0.5,0.5,0.5)` | olive `(0.5,0.5,0)` |
| sheet frame | grey `(0.75,0.75,0.75)` | grey `(0.75,0.75,0.75)` |

Filtering to geometry ink *before* polygonizing removes causes 2 and 5 at the source
rather than fighting them with thresholds.

## Plan

### Phase A — ground truth and an eval harness  *(first; everything else is unmeasurable without it)*
- `tests/fixtures/ground_truth.json`: for each sample drawing, the true cutouts — shape,
  real-world size, quantity — plus the true sheet scale. Confirmed by Maoz, not by me.
- `eval.py`: runs the pipeline over every fixture and reports per-drawing **recall**
  (cutouts found), **precision** (cutouts invented) and **dimension error** (mm).
- Lock it into CI as a test that fails on regression.

### Phase B — separate geometry ink from annotation ink  *(the structural fix)*
- Classify each `get_drawings()` path by stroke colour and width into geometry / annotation
  / frame. Detect the geometry colour per page (darkest dominant stroke) rather than
  hardcoding black — not every exporter uses the same palette.
- Polygonize geometry ink only.
- Kills: the Ø glyph, leader-line triangles, arrowheads, dimension lines.
- Keep annotation ink: it is what Phase E reads dimension labels from.

### Phase C — gate on the part outline
- The largest geometry loop that is not the sheet frame is the part outline.
- A cutout must lie strictly inside it. Anything in the margins, title block or the
  isometric preview is not a cutout.

### Phase D — fix the classifier
- Replace the `RECT_FIT_THRESHOLD` gate with nearest-ideal-area (circle vs rectangle vs
  obround). **This already exists** in `app/bom/shapes.py` — reuse it in `_classify`
  instead of writing it twice.
- Rework `contains_text`: penalise only when the text is large relative to the candidate.
  A dimension label inside a Ø290 bore must not be treated as an annotation box.

### Phase E — recover the real-world scale
- Parse `Scale 1:N` from the sheet **and** from the title block.
- Independently infer scale by matching Ø / linear dimension labels to measured geometry
  and taking the median ratio. (The existing `DIA_RE` finds nothing on these drawings —
  it must be fixed to match `Ø 235 THRU`, `Ø75 THRU`, `Ø551`.)
- **Cross-check.** Where the two disagree, trust the geometry-derived value and flag the
  page for review. A silently wrong scale is worse than an admitted unknown.
- Store the scale on the page; apply it to every measured dimension and cut length.

### Phase F — re-measure and iterate
Run the harness, look at what still fails, fix that. Repeat until the numbers stop moving.
Report where we actually land instead of claiming a round number.
