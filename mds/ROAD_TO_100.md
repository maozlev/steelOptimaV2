# What it takes to be 100% accurate

Written 2026-07-25, after building the accuracy gate. Every number here was measured this
session, not estimated.

---

## The headline

**You are about two days from "100%" as the project currently defines it, and that number
would be a lie.**

The current definition is *recall and precision on nine drawings, with sizes checked at 5%
tolerance (30% on the one scan)*. Two fixes take that to 100%. It would still not mean the
software cuts the right parts, because three of the five things "accurate" has to cover are
not measured at all.

So this plan has two halves: **close the gaps** (short), and **make the measurement worth
trusting** (longer, and the part that actually matters).

---

## Part 0 — What "100% accurate" has to mean

A cutout is right only if all five are right. Current status:

| # | Dimension | Measured today? | Where it stands |
|---|---|---|---|
| 1 | **Recall** — every real cutout found | Yes | 98% macro. One gap: A5.png, 14/17 |
| 2 | **Precision** — nothing reported that is not a cutout | Yes | 100% macro |
| 3 | **Size in real mm** | Loosely | 5% tolerance on vector; **30% on raster.** A 30% size error passes as a hit |
| 4 | **Shape class** (circle / rectangle / slot / notch) | Implicitly | Only as part of matching; never scored on its own |
| 5 | **Cut length** — the burn distance, i.e. the quote | **No** | No ground truth exists. Never compared to anything |

**Item 5 is the one that gets invoiced.** The BOM's whole purpose is cut length, and it is the
only output with no ground truth at all. Item 3's raster tolerance is the second hole: A5.png's
Ø12.5 bolt holes read Ø9.2 and the fixture calls that a hit.

Fixing 1 and 2 while 3, 4 and 5 stay unmeasured is how a system reports 100% and ships wrong
parts.

---

## Part 1 — The cliffs: silent total losses

These do not degrade accuracy, they delete it. All three are live.

### 1.1 A scanned drawing with five vector strokes on it loses EVERY hole

Measured this session:

```
 0 strokes -> page classifies as raster -> 4 of 4 holes found
 4 strokes -> raster -> 4 of 4
 5 strokes -> MIXED  -> 0 of 4        <- cliff
20 strokes -> MIXED  -> 0 of 4
```

`classify_page` returns `mixed` when a page has a full-page image **and** ≥5 vector paths;
`extraction/service.py:28` branches only on `kind == "raster"`, so a mixed page takes the
vector path and the scanned image is never CV-processed. Nothing warns. The BOM is empty and
looks confident.

Five paths is nothing: a sheet border, a signature stamp, a page number, a redaction box, a
CAD title block over a scanned body. This is the most likely way a real customer file returns
zero holes.

**Fix:** run the raster pipeline on `mixed` pages too and merge with the vector candidates
(dedupe already handles overlap). **~20 lines. Highest value item in this document.**

### 1.2 Cropping destroys the scale

Known, unfixed, `extraction/service.py`. The crop tool removes the title block and margins —
exactly where the printed scale and dimension lines live. Cropping Doc_HK3573 turns a
confident 1:5 into an unverified 1:16.81. Your call was that you will not crop; the next
operator will, because the UI invites it. The original survives at `originals_dir/{sha}.pdf`,
so resolve the scale from that while extracting geometry from the crop. **~15 lines.**

### 1.3 A finalized document goes stale and nothing says so

Not theoretical — Doc_HK3573 is finalized right now with **two real bolt holes missing**,
auto-rejected at 0.38/0.40 by the 2026-07-16 pipeline. `tools/export_fixture.py` now refuses
to export such a document, but the app itself still shows and exports it as final.

**Fix:** store the pipeline version on the job; mark documents whose version is behind and
show it in the workspace. **~30 lines.** Then re-run and re-review Doc_HK3573.

---

## Part 2 — Close the measured gaps

| # | Gap | Buys | Cost |
|---|---|---|---|
| 2.1 | **Ink-crossed holes.** A5.png 14/17. Annotation drawn through a hole shatters its interior | macro recall 98% → **100%** | 1–2 days |
| 2.2 | **Raster size bias.** Raster measures the ink INTERIOR: Ø12.5 reads Ø9.2 (−26%). Compensate by the measured stroke width | fixture tolerance 0.30 → ~0.05, i.e. dimension 3 becomes real on scans | ~1 day |
| 2.3 | **Diameter from circumradius, not area.** `π·d` inherits the polygon's area deficit: −1.3% at 16 sides | removes a one-directional bias in size AND cut length | ~2 h + re-baseline |
| 2.4 | **Split-contour gap closing.** A contour emitted as separate per-segment paths is lost at any gap ≥0.5pt | closes a whole-hole failure with a narrow trigger | ~4 h |

2.1 and 1.1 together are what "100% recall" actually requires. Both have datasets now
(`tools/eval_ink_crossing.py`, and 1.1 is a one-line fixture).

---

## Part 3 — Make the measurement worth trusting

This is the half that separates a real 100% from a scoreboard.

### 3.1 Put NOMINAL sizes in ground truth, not measured ones

Fixtures exported from reviewed documents are marked `"sizes": "measured"` — they are the
pipeline's own numbers. They pin existence and position exactly and **cannot catch a scale
error**, because a wrong scale is baked into both sides of the comparison.

Where the drawing prints a size (Ø235 THRU, 16 and 4.50), type it in and set
`"sizes": "confirmed"`. Only then does the fixture defend dimension 3. Cheap, per drawing,
and it is the difference between a size test and a tautology.

### 3.2 Give cut length ground truth

Dimension 5 has none. For each fixture cutout, record the expected burn length from its
nominal size (π·d, 2(L+W), 2(L−W)+πW, and the notch's true burn excluding its mouth) and
score it like recall. **This is the number you quote.** ~half a day, and it would have caught
2.3 without a probe.

### 3.3 Tighten the tolerances until they bite

5% is loose for steel. Once 2.2 and 2.3 land, take vector to 1% and raster to 5%, and let the
gate tell you where the pipeline actually is. A tolerance nothing ever fails is not measuring.

### 3.4 Forbidden regions on all nine drawings

Precision is currently *inferred* from count-matching. Only rejected cutouts from a review
produce regions automatically, and there are none yet. Drawing 2–4 rectangles per sheet by eye
(title block, callout boxes, projection symbol) turns precision into an asserted property.
~half a day for all nine.

---

## Part 4 — The statistical problem, which is the real one

**Nine drawings. 483 cutouts, but 293 of them are one drawing's identical holes. 25% of the
ground truth was wrong at one point.** One drawing (A5.png) is the only scan.

100% on nine samples is a statement about nine samples. The coloured-layer bug proved it this
week: all nine scored 100% while a whole class of CAD sheet returned zero holes.

**Target ~25 real drawings**, chosen for spread rather than count:

- 3–5 more **scans** (the weakest arm by far, and the one customers actually send)
- 2–3 with **coloured layers** (now handled, still unproven on a real sheet)
- 2–3 **multi-view** sheets (plan + elevation + section — does a hole get counted twice? **No fixture answers this**)
- 2–3 with **real notches**, including a small one and a semicircular one — the notch detector has **exactly one** real positive example in the entire repo
- 2–3 **mixed** pages (vector title block over a scanned body — see 1.1)

This is now cheap: `tools/export_fixture.py --all --merge` after each review session makes
every drawing you look at a permanent fixture. The constraint is your drawings and your eyes,
not code.

---

## Part 5 — The one unknown that must be understood, not tuned

**A (3)'s dimension-line measurement runs a few % long.** Its labels imply 7.23 / 7.53 / 7.66
where the truth is 7.75 — a 6% spread. The resolver correctly refuses to be confident and asks
the operator, which is the right behaviour, but the root cause is unknown (probably arrowhead
overshoot on the measured line length).

**Do not tune against A (3) until it is understood.** A 6% scale error is a 6% part error, and
this is the only sheet that exhibits it — which means it is either a bug that will appear on
other large sheets, or a property of that plot. Nobody knows which.

---

## Part 6 — The structural lever: take DXF as INPUT

Everything above is inference from a lossy picture. A PDF is a printout; a DXF carries geometry
and layers outright. For any customer who can send DXF, cutouts are **exact by construction** —
no ink separation, no scale resolution, no convention detection, none of the five failure
modes above.

`export.py` already comments about DXF consumers and emits JSON only, so DXF is on the list as
an *output*. As an **input** it is worth more: it converts a hard inference problem into a
parsing problem for whatever share of your customers can supply one.

If even a third of jobs arrive as DXF, that third is 100% accurate the day it ships. Ask
customers before building more CV.

---

## Ordering

| | Item | Cost | Why here |
|---|---|---|---|
| 1 | 1.1 mixed pages | ~20 lines | Silently loses every hole on a whole file class |
| 2 | 1.3 stale documents + re-review Doc_HK3573 | ~30 lines | Two real holes missing from a finalized doc today |
| 3 | 2.1 ink-crossed holes | 1–2 d | The last measured recall gap; dataset exists |
| 4 | 3.1 nominal sizes | hours | Makes the size test stop being a tautology |
| 5 | 2.2 raster size bias | ~1 d | Retires the 30% tolerance |
| 6 | 3.2 cut-length truth | ~½ d | The number you invoice, currently unmeasured |
| 7 | 2.3 circumradius + 2.4 split contour | ~6 h | Both already pinned as strict xfails |
| 8 | 1.2 crop trap | ~15 lines | Waiting for the next operator |
| 9 | 3.3 tighten tolerances, 3.4 forbidden regions | ~1 d | Let the gate bite |
| 10 | Part 4: grow to ~25 drawings | ongoing | The only item that makes 100% mean something |
| 11 | Part 6: ask about DXF input | a phone call | Possibly the highest leverage on this page |

Items 1–3 are roughly **three days** and give a defensible 100% recall / 100% precision.
Items 4–9 are another **week** and make the sizes and cut lengths trustworthy. Item 10 never
finishes, and that is correct.

---

## What "done" will mean — and what it still will not

**Done:** on ~25 real drawings including scans, coloured layers, multi-view sheets and real
notches, the pipeline finds every cutout, reports nothing spurious, sizes to 1% (5% on scans),
and cut lengths that reconcile with nominal geometry — with a test that goes red in both
directions if any of that moves.

**Still not:** a guarantee about the next drawing. The coloured-layer bug scored 100% on nine
drawings right up until it was probed. The defence against the next one is not a bigger score,
it is the habit that found this one: **when something looks wrong, probe the decision point
directly.** That is what every real fix in this project has come from.
