# Synthetic drawing corpus — plan

> **STATUS (2026-07-25): mostly NOT built, deliberately. Read this box before working from
> the matrix below.**
>
> One slice shipped — the ink-crossing arm (H7/H8/H9), as `tools/eval_ink_crossing.py` and
> `tests/test_ink_crossing.py` — because that failure class had exactly three examples in the
> repo and cannot be fixed from three examples.
>
> The rest was displaced by **`tests/test_thresholds.py`**: 54 probes that drive each
> constant the pipeline branches on directly. They cover axes A, D, F and G below at a
> fraction of the cost, and they found things the corpus was predicted to find — including
> D9, the coloured-layer bug, in ten minutes.
>
> The prediction at the bottom of this document ("first run should fail on D9…") was scored:
> **D9 was real and is now fixed.** The rest of the matrix stays here as a menu, not a plan.
> Build a slice when there is a specific failure class to characterise. Volume was the wrong
> instinct; aim was the right one.


Build a generator that invents engineering drawings with **exact ground truth by
construction**, so hole detection can be tested on hundreds of cases instead of nine, and
driven to 100% on the ones we control.

Companion to `mds/TEST_PLAN_HOLES.md` (which covers the real-fixture tests). This document
is only the synthetic corpus.

---

## I was wrong to rank this last

Last pass I put the generator at the bottom. One thing changes that: **backlog item #2, the
ink-crossed holes, has exactly three examples in the entire repo** — the three bolt holes on
A5.png with a leader arrow and a dimension arc drawn through them. You cannot fix a failure
class from three examples; you can only overfit to them.

A generator produces that failure **on demand, parameterised** — crossing angle, offset from
centre, line width, hole diameter, DPI — which is a real dataset for a real fix. Same for the
raster size bias (item #3): sweep DPI and stroke width, and the bias becomes a measurable
curve instead of one number.

That is the strongest reason to build this, and it is not the reason I gave before.

---

## Two rules that keep this from doing damage

**1. The real fixtures always win.** A synthetic case may only be "fixed" if
`tools/eval_detection.py` on the nine real drawings stays at **macro 98% recall / 100%
precision or better**. Tuning a threshold until synthetics go green while a real sheet
regresses is the exact mistake this project has already made three times (the reverts in
CLAUDE.md). Both corpora run in the same command; the real one is the gate.

**2. Green on 500 synthetic cases is not accuracy.** These sheets are drawn by me, so they
encode *my* model of a drawing. They cannot invent a drafting convention I have not thought
of. This corpus is for **regression protection and debugging**, and it is a genuinely good
one for that. It is **not** validation, and it does not retire the "get ~10 more real
labelled drawings" item.

---

## Architecture

```
server/tools/synth/
  primitives.py   circle / rect / obround / polygon / bezier-circle / n-gon emitters
  sheet.py        Sheet: page size, ink convention, frame, title block, dim lines
  decoys.py       the things that must NOT be detected
  degrade.py      the raster arm: render → JPEG / noise / skew / blur / ink-crossing
  cases.py        THE CASE MATRIX — declarative, one entry per case
  build.py        CLI: writes <out>/*.pdf + <out>/ground_truth.json
```

**The load-bearing design decision: ground truth is derived from the *declared parameters*,
not from the drawn geometry.** A case says "circle, d=10pt, at (200,200), sheet scale 5.0" →
the drawer renders it and the truth writer emits `diameter_mm: 50.0, center: [200,200]`
independently. If both read back the drawn polygon, a bug in the emitter would produce
matching-but-wrong truth and the test would pass while the drawing was garbage.

**Emit `ground_truth.json` in the existing schema** (`server/tests/fixtures/ground_truth.json`)
so `tools/eval_detection.py` scores the corpus **unchanged** — add a `--pdfs/--truth` pair of
flags, nothing more. One harness, one definition of recall. Do not write a second scorer.

Synthetic cases get the fields the real fixtures cannot cheaply have — `center_mm`,
`forbidden_regions` (TEST_PLAN_HOLES T1/T2) — for free and exactly.

**The corpus is generated, not committed.** The generator is seeded and deterministic; 500
PDFs do not belong in git. Commit `cases.py`; regenerate on demand.

---

## The case matrix

Every case is emitted in **both ink conventions** (colour-coded and width-coded) unless the
case is specifically about the convention. Counts below are before that ×2.

### A — shape primitives: can it find the hole at all

| # | Case family | Variants | Targets |
|---|---|---|---|
| A1 | Circle diameter sweep | 1, 2, 3, 5, 10, 25, 50, 100, 200, 400 pt | `MIN_CUTOUT_AREA_PT2 = 4.0` floor — where does a small hole stop being found? |
| A2 | Rectangle aspect sweep | 1:1, 2:1, 5:1, 10:1, 20:1, 50:1 | `LOOP_MIN_THINNESS = 0.15`, `RECT_FIT_THRESHOLD = 0.95` |
| A3 | Obround slot aspect sweep | same six | `SLOT_FIT_THRESHOLD = 0.90` |
| A4 | Rotation of A2/A3 | 0, 15, 30, 45, 60, 90° | min-rotated-rect fit must be angle-invariant |
| A5 | Regular polygons | hex, octagon, square, triangle | hex bolt holes are real; triangle must classify freeform, not slot |
| A6 | Freeform | L, T, cross, keyhole, D-shape | must be found (score below auto-approve is fine — never dropped) |
| A7 | Concentric / counterbore | 2 and 3 nested circles | `NESTED_MAX_RATIO = 0.95`, `DUPLICATE_IOU = 0.40` |
| A8 | **Bore-to-part ratio sweep** | 50, 70, 78, 85, 88, **90**, 92, 95% | `MAX_CUTOUT_PARENT_RATIO = 0.90` — **the boundary that killed the gasket bore twice.** Sweep it, pin where it flips |
| A9 | Notch shapes | rect, obround, semicircular, V, corner-L, keyhole-slot open to edge | `_notch_candidates`; V must be REJECTED (it is a chamfer), the rest found |
| A10 | Notch depth sweep | 0.3, 0.5, 0.8, **1.0**, 1.5, 3, 8, 14% of part | `NOTCH_MIN_HOST_FRAC = 0.01` boundary. **The notch detector has one real example — this is where it gets a hundred** |

~60 cases.

### B — count and density

| # | Case | Targets |
|---|---|---|
| B1 | 1 / 4 / 16 / 64 / 293 / 1000 holes | scale-out; 293 mirrors A (4) |
| B2 | Bolt circle, 16 holes at 22.5° | the positional invariant from TEST_PLAN_HOLES T2.2 |
| B3 | Two concentric bolt circles | ambiguity between patterns |
| B4 | Hole pitch sweep: 20, 5, 2, 1, 0.5 pt gap between rims | do adjacent holes fuse? |
| B5 | Part with **zero** holes | must report zero, must not invent one |
| B6 | Empty sheet, frame + title block only | must not crash; must report zero |

~20 cases.

### C — decoys: everything that must NOT be reported

This is the precision half, and it is where the real drawings have burned this project most.

| # | Decoy | Why it is dangerous |
|---|---|---|
| C1 | `Ø290 THRU` as live text | the glyph counter — a `Ø` *is* a circle |
| C2 | **Same label converted to vector outlines** | the real killer; no OCR word box to veto it |
| C3 | Title block grid | cells are rectangles |
| C4 | First-angle projection symbol (2 concentric circles) | scored 0.98 as a hole on Doc_HK3573 |
| C5 | ⊕□1 feature-control frame | scored as a slot |
| C6 | Boxed dimension callout | was reported as 5 slots on the gasket |
| C7 | Dimension + extension lines, arrowheads (fill-only) | `_is_construction` |
| C8 | Dashed centre lines, bolt-circle centreline | `_is_dashed` |
| C9 | Sheet border | `FRAME_AREA_RATIO = 0.5` |
| C10 | Revision cloud | named in the 117-626-141_4 fixture |
| C11 | Gear teeth (10, 30, 53 teeth) | concavities that are the part's shape |
| C12 | Corner chamfers 5/10/20mm | named in the 12562 fixture |
| C13 | Section hatching, solid fill | fragments the planar arrangement |
| C14 | Weld symbols, surface-finish triangles, datum flags | ordinary sheet furniture, untested |
| C15 | **Detail bubble** — big circle with "A" inside, plus its magnified detail view | a large circle that is emphatically not a hole. **No fixture covers this and it would be reported today** |
| C16 | **Second view of the same part** (plan + elevation + section) | the same hole drawn twice on one sheet. Does it double-count? **No fixture answers this** |
| C17 | Scale bar, north arrow, company logo | logos are arbitrary closed shapes |

Each decoy also gets a `forbidden_regions` rect in the truth file, so the test asserts
"nothing here", not just a count. ~70 cases (decoys alone, and in combination with A/B parts).

### D — ink convention (`ink.py`)

| # | Case | Expected |
|---|---|---|
| D1 | Clean colour convention: geometry 0.0, annotation 0.5, frame 0.7 | colour path |
| D2 | Clean width convention: all black, 0.7 vs 0.35 | width path |
| D3 | **One stray coloured stroke on a black page** | must still take the **width** path — `MIN_ANNOTATION_SHARE = 0.05`. This is the bug that cost a cycle |
| D4 | Coloured-stroke share sweep: 1, 3, **5**, 8, 20% | pin where the decision flips |
| D5 | Single stroke width everywhere, all black | fail-safe: everything is geometry, **never blind** |
| D6 | Inverted convention (geometry thin, annotation thick) | must degrade to fail-safe, not to zero holes |
| D7 | Width ratio sweep 1.0, **1.2**, 1.5, 3.0 | `THIN_TO_THICK_RATIO = 1.2` |
| D8 | Geometry in dark grey 0.25 (A (3)'s convention) | `GEOMETRY_MAX_CHANNEL = 0.4` |
| D9 | Geometry drawn in blue / red / green | **untested today** — a coloured layer is normal CAD practice and `max(r,g,b)` sends pure red to *frame* |

~25 cases. **D9 is a likely real bug.** `max((1,0,0)) = 1.0 ≥ FRAME_MIN_CHANNEL`, so a part
drawn in red is classified as sheet border and thrown away. No real fixture uses colour that
way; a customer will.

### E — scale (`scale.py`) — the most dangerous bug class in the system

A wrong scale does not look broken; it quietly cuts parts the wrong size.

| # | Case | Expected |
|---|---|---|
| E1 | Scale sweep 1:1, 1:2, 1:5, 1:10, 1:20, 1:50, 2:1, 5:1 | real-world mm identical across all |
| E2 | Printed scale correct + dim lines agree | auto-confirm, no operator prompt |
| E3 | **Printed scale lies** (block says 1:1, geometry says 1:2) | geometry wins, flagged |
| E4 | No printed scale, 3 agreeing labels | inferred — `MIN_AGREEING_LABELS = 3` |
| E5 | No printed scale, 2 labels | **unresolved — must ask, not guess** |
| E6 | Labels disagreeing by 2 / 5 / 10% | `RATIO_AGREEMENT = 0.02` — must refuse confidence (the A (3) case) |
| E7 | Huge sheet 2540×1504mm, labels 54–95pt from their lines | `LABEL_REACH_IN_TEXT_HEIGHTS = 3.0` |
| E8 | Diameter callout on a leader to its bore | `LEADER_ATTACH_PT`, `LEADER_TIP_PT` |
| E9 | "Scale" and "1:5" far apart in the text stream, adjacent on the page | the spatial-matching trap |
| E10 | **Operator types 1:50 on a 1:5 sheet** | the cross-check must reject it — 10× oversize parts |

~30 cases.

### F — CAD-export pathologies

| # | Case | Targets |
|---|---|---|
| F1 | Circle as unclosed polyline, gap 0.5 / **1.5** / 3 / 8 pt | `LOOP_CLOSE_TOL_PT = 1.5` |
| F2 | Circle as beziers | `BEZIER_SAMPLES = 8` |
| F3 | Circle as 8- / 16- / 32- / 64-gon | cut length must use π·d, not the polygon perimeter |
| F4 | Contour split across 2, 4, 12 separate paths | path chaining |
| F5 | Exact double-stroke of the outline | `DUPLICATE_IOU = 0.40` |
| F6 | Page rotation 0 / 90 / 180 / 270 | `page.rotation_matrix` — a documented landmine with **no test** |
| F7 | Part outline as a planar face only, never a closed loop | the 12562 case; `_structural_parts` |
| F8 | Two overlapping holes (10 / 40 / 80% overlap) | |
| F9 | Hole exactly on the part boundary; hole outside the part | the paper-vs-metal gate |
| F10 | Coordinates far from origin, tiny/huge page | numeric robustness |

~35 cases.

### G — multi-part sheets

2 parts / 5 parts on one sheet · one part inside another's bbox · parts of very different
sizes (`MIN_PART_AREA_FRAC = 0.02` boundary sweep: a small part at 1, **2**, 5, 20% of the
biggest — does the small one get dismissed as a title-block symbol?). ~15 cases.

### H — the raster arm

**Every vector case above can become a raster case for free**, with the same exact ground
truth: render it and re-ingest. This is where the corpus earns its cost.

| # | Degradation | Sweep |
|---|---|---|
| H1 | Clean render | 150 / 200 / 300 / 400 / 600 DPI |
| H2 | JPEG compression | quality 90 / 70 / 50 |
| H3 | Gaussian noise | σ = 2 / 5 / 10 |
| H4 | Skew | 0.2 / 0.5 / 1 / 2° |
| H5 | Blur | 0.5 / 1 / 2 px |
| H6 | Threshold/contrast shift | scanner variation |
| H7 | **Ink crossing a hole** | crossing angle 0–90° × offset 0/25/50/75% of radius × line width 0.35/0.7/1.4 × hole d=10/25/50pt |
| H8 | Leader arrowhead landing inside a hole | the exact A5.png Ø12.5 failure |
| H9 | Dimension arc passing through a bolt circle | the other A5.png failure |

H7 alone is ~100 combinations and is **the dataset backlog item #2 needs**. H1 across DPI is
the measurement backlog item #3 needs: plot measured/true diameter against DPI and stroke
width and the "raster reads the ink interior" bias becomes a correction formula rather than a
0.30 tolerance.

~250 raster cases, generated combinatorially from ~15 base drawings.

---

## Totals

| Axis | Cases |
|---|---|
| A shapes | 60 |
| B count/density | 20 |
| C decoys | 70 |
| D ink convention | 25 |
| E scale | 30 |
| F CAD pathologies | 35 |
| G multi-part | 15 |
| H raster | 250 |
| **Total** | **~505** from ~90 declarative specs |

---

## Build order

| Phase | Content | Cost | Deliverable |
|---|---|---|---|
| 1 | Skeleton, primitives, both conventions, truth writer, `eval_detection --pdfs/--truth` | ~4 h | A (subset) + D running end to end; **first failure list** |
| 2 | Full A + B | ~4 h | shape and density coverage |
| 3 | C decoys + `forbidden_regions` assertions | ~1 day | precision measured, not inferred |
| 4 | F pathologies + G multi-part | ~4 h | rotation invariance finally tested |
| 5 | E scale | ~4 h | the most dangerous bug class covered |
| 6 | H raster arm incl. ink-crossing generator | ~1 day | the dataset for backlog #2 and #3 |
| 7 | Triage + fix loop | open-ended | drive to 100% |

Roughly **4 days** to a complete corpus. Phase 1 delivers signal in half a day — build it
first and look at what fails before committing to the rest.

---

## The fix loop (phase 7)

1. `uv run python tools/synth/build.py --out ../synthetic_drawings`
2. `uv run python tools/eval_detection.py --pdfs ../synthetic_drawings --truth ../synthetic_drawings/ground_truth.json --by-tag`
3. Failures bucket by case tag. **Fix the biggest bucket, not the first failure** — 40 cases
   failing usually means one threshold, not 40 bugs.
4. Re-run **both** corpora. Real fixtures must not move. If a synthetic fix costs real recall,
   **the synthetic case is wrong** — the real drawing is the authority. Delete or re-label it.
5. Known-unfixed cases go in an **expected-fail registry with a mandatory `why` string**, same
   spirit as the strict xfails in TEST_PLAN_HOLES. A case that fails silently is worse than no
   case.

**Predicted first-run failures** (worth writing down now so we can score the prediction):
D9 coloured geometry, C15 detail bubble, C16 double-counted second view, A1 at d ≤ 3 pt,
A10 near the 1% notch boundary, and most of H7. If those are the failures, the corpus is
aimed correctly. If everything passes on the first run, **the corpus is too easy and I built
it wrong.**

---

## What "100%" will mean when this is done

"On ~505 drawings whose answers are exact by construction, plus 9 real drawings, detection
finds every cutout and reports nothing that is not one — and no change can alter that without
a red test."

That is a much stronger claim than today's. It is still **not** "100% on the next customer's
drawing", and no amount of synthetic data will make it so.
