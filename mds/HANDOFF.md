# SteelOptima V2 — state of play (2026-07-13)

Read this first. It is written for a session that has never seen the project, and it is
deliberately biased toward what is **not** obvious from the code: the wrong turns, the bugs
that hid behind plausible numbers, and the facts confirmed by Maoz rather than assumed.

---

## What the system is

Upload an engineering drawing (PDF / JPEG / PNG). It finds the manufacturing cutouts —
holes, slots, notches, freeform openings — measures them in **real-world millimetres**, and
produces a bill of materials with quantities and **cut length** (the burn distance, which is
what cutting actually costs). The operator reviews on a canvas, finalizes, exports.

**Stack.** FastAPI + SQLite + in-process asyncio worker + WebSocket job events; React 19 +
Vite + Tailwind. Ollama (`qwen3.5:9b`). Single workstation — do **not** add Redis, Celery or
Postgres.

**Pipeline.** ingest → classify page → render 300 DPI → *(optional crop — see the trap
below)* → **separate geometry ink from annotation ink** → polygonize → **gate on the part
outline** → classify shapes → score → **resolve the sheet scale** → VLM rescue + veto →
human review → finalize → export.

> A separate **material-table / projects pipeline** (`app/tables/`, `app/api/projects.py`,
> `app/api/tables.py`) is being built by Maoz in parallel. It is not covered here.

---

## Where accuracy stands

### **100% recall · 100% precision**, per drawing.

| Drawing | Part | Scale | True | Found | Recall |
|---|---|---|---|---|---|
| 117-626-141_1 | Flange | 1:5 | 2 | 2 | 100% — incl. the notch |
| 117-626-141_4 | Plate | 1:2 | 2 | 2 | 100% |
| 12562-3000F501023 | Plate | **2:1** | 2 | 2 | 100% |
| 333-532-294 | Washer | 1:3 | 1 | 1 | 100% |
| ASH-071222 | Gear | 1:3.5 | 1 | 1 | 100% |
| A (3) | Beam | 1:7.75 | 148 | 148 | 100% |
| A (4) | Beam | 1:1 | 293 | 293 | 100% |
| Doc_HK3573 | Gasket | 1:5 | 17 | 17 | 100% |

**Every drawing finds every cutout and invents none.** The last gap — the flange's notch, a
whole feature class of cuts open to the part's edge — closed on 2026-07-14 (see §7 below).
Treat the number with respect, not comfort: eight drawings, and the notch detector has
exactly one real positive example.

### The most important file in the repo

`server/tests/fixtures/ground_truth.json` — the right answers, **confirmed by Maoz against
the drawings** — plus `server/tools/eval_detection.py`, which scores the pipeline against
them.

```bash
cd server && uv run python tools/eval_detection.py
```

Without this, "accuracy" is a feeling. **Every significant fix in this project was found or
validated by that harness, and three were reverted by it.** Run it before and after any
change to extraction.

It reports **micro** (per-cutout) and **macro** (per-drawing) separately on purpose: micro
reads ~100% partly because A (4) contributes 293 identical holes. **Macro is the honest
number.** I nearly reported the flattering one on day one.

### Ground truth was wrong twice, and Maoz caught both

- **Doc_HK3573** was recorded as "18 holes + 5 slots" from old notes nobody checked. It is a
  gasket: **16 bolt holes + 1 central Ø605 bore, and no slots at all.** I had been tuning
  thresholds against a wrong answer — I even *lowered* one to "recover" a slot that never
  existed.
- **A (3)**'s sizes were unknown until Maoz said the holes are **Ø40**, which back-solves the
  sheet scale to 1:7.75 and reproduces the slot sizes independently.

**The limiting factor on this project is labelled drawings, not algorithms.** Eight samples
is a tiny set, and 25% of its ground truth was wrong. Ten more real drawings with confirmed
answers will tell you more about true accuracy than any amount of tuning.

### Rules Maoz confirmed

- A cutout is: an **enclosed hole** (circle / rectangle / obround slot) — YES. A **notch cut
  into the part**, open to its edge — YES. The **outer profile, chamfers, gear teeth** — NO,
  that is the shape of the part.
- **Never miss a real hole.** A false positive costs a click; a missed hole costs a part.
  When in doubt, surface it — never silently drop it.

---

## The seven load-bearing ideas

### 1. Decide how to READ the drawing, before polygonizing anything

The original extractor polygonized *every stroke on the page* — part edges, leader lines,
arrowheads, glyph outlines — into one soup, then tried to sort cutouts back out by shape.
**That cannot work.** The `Ø` in a label "Ø290 THRU" is drawn as vector paths and *is*,
geometrically, a circle. On the gear it was auto-approved at 0.98 as a Ø3.1 hole **while the
real Ø290 bore was rejected**. No threshold separates a glyph from a hole, because they are
the same shape. But the draughtsman already marked which ink is which.

**`app/extraction/ink.py`** decides, per page, from evidence on the page:

1. **Colour** — `max(r,g,b)`: `< 0.4` geometry (part edges) · `>= 0.6` frame (sheet border,
   discarded) · between → annotation (dimension/leader lines). Fill-only paths (arrowheads)
   are always annotation.
2. **Is the page *actually* colour-coded?** Only if a meaningful share of its *strokes* land
   in annotation. **This test is the whole ballgame** — see the mistake below.
3. **Stroke width** — if colour says nothing, fall back. ISO draws part edges **thick**,
   dimension / extension / leader / centre lines **thin**.
4. **Fail safe** — if neither convention holds, treat all non-frame ink as geometry. Noisier,
   but **never blind**.

Half the drawings take each path:

| colour-coded | width-coded |
|---|---|
| Flange, Plate, 12562, Gear | Washer, A (3), A (4), Gasket |

**Diagnostic — run this first on any new drawing:**

```bash
uv run python tools/inspect_ink.py                    # every sample
uv run python tools/inspect_ink.py ../pdfs/foo.pdf    # one file
```

It prints which convention was chosen, **the evidence for it**, the ink split, what was
detected, and the scale. A wrong convention decision looks like a detection bug everywhere
downstream.

### 2. A cutout is cut out of the PART

Doc_HK3573's title block holds a "First Angle Projection" symbol (two concentric circles)
and a `⊕□1` feature-control frame (a square). They are drawn in thick black ink and they
**are**, geometrically, circles and a square. They scored 0.98 as holes. **No shape rule will
ever say otherwise.** What makes them not-holes is *where they sit*: on the paper, not in the
metal.

So candidates must lie inside a **part outline** = a top-level closed loop that is **big**
**and contains something**. Both halves are load-bearing, and I learned each the hard way:

- Without the **size** test, the title-block symbols are themselves top-level loops with
  nothing around them — they qualify as parts and cheerfully admit their own insides. My
  first attempt did nothing at all.
- Without **"contains something"**, 12562 breaks: its octagonal outline is only a planar
  face, never a closed loop, so the sheet's only loops are the two **slots** — the code
  declared *the slots* to be parts and threw one away. That cost 7% recall and made me revert
  the whole idea once.

**A part outline we cannot see is better than one we invent:** when this finds nothing, the
filter simply does not run.

### 3. Scale, or you ship wrong parts

Every measurement is in **paper** millimetres. On a 1:3.5 sheet the gear's Ø290 bore measures
82.9 mm of paper, and the BOM reported exactly that. **This is the most dangerous class of bug
in the system: it does not look broken, it just quietly produces parts of the wrong size.**

`app/extraction/scale.py`. A label like "624" is not merely *near* the flange's width — it is
written **on the dimension line that spans it**, and that line is 624 mm of reality long. So
`scale = label / length of the line it sits on`, which is self-checking: a sheet has one scale
and every dimension line must agree. Diameter callouts are followed **down their leader** to
the bore they name.

**The printed scale only cross-checks. It never decides, because title blocks lie:**

- ASH prints "Scale 1:3.5" on the sheet and **"SCALE:1:5"** in its own title block.
- The plate prints 1:2 and **1:1**.

They are stale template defaults. Believing them cuts parts 40% oversize.

**Two subtle bugs worth remembering:**

- The title block's "Scale" and its value "1:5" are **adjacent on the page but nowhere near
  each other in the PDF's text stream** (tokens 10 and 99). A regex over flattened text found
  nothing. Scale text is matched **spatially**.
- A label's distance from its line **scales with the sheet**. A (3) is plotted on 2540×1504 mm
  — six times an A3 — and its labels sit 54–95 pt from their lines against a hardcoded 36 pt
  limit. So every big dimension was discarded as "too far", and one mismatched label invented
  a scale of 2.45. Reach is now measured in the label's own **text heights**.

### 4. The operator owns the scale — and the detector checks them

Maoz's call: the scale is the user's responsibility. But *on its own that is not safer* — a
mistyped **1:50** on a 1:5 sheet cuts every part **ten times too big**, silently. The safety
never came from *who* supplies the number; it came from **two independent sources having to
agree**. So the roles swapped rather than one being removed:

- The operator sets the scale, and it stays editable.
- The detector's estimate is kept (`Page.scale_detected`) and **cross-checked** against what
  they typed: *"you set 1:50, but this drawing's own dimensions say 1:5 — that is 10.0× out."*
- **Finalize refuses** any page whose scale nobody — machine or human — can vouch for.
- **But it does not nag.** Where the drawing *proves* its own scale (a printed value that its
  dimension lines independently reproduce), it auto-confirms. Making the operator click to
  concur with two sources that already concur adds friction and no safety. Only A (3), whose
  labels disagree by 6%, actually asks.

### 5. Shape is measured against ideal shapes, not areas

The DB `kind` enum stores a true rectangle and an obround slot **both as "slot"** — they clear
the same gate. A BOM must tell them apart: different cut lengths, different tooling. The
displayed shape is derived from geometry by **IoU against the ideal rectangle and the ideal
obround** (`app/bom/shapes.py`).

Comparing **areas** instead of shapes was my first attempt and it made the gasket report
**47 slots instead of 5**. An arbitrary blob can share an obround's area ratio without being
one.

### 6. Overlap is not duplication; and cut length comes from the ideal shape

A gasket's Ø605 bore fills **78%** of the Ø686 ring around it. The dedupe dropped anything
overlapping a kept shell by >40% IoU — a rule written to kill concentric countersink strokes —
so **the system deleted the central hole precisely because the part is a ring.** Only a
near-identical *restroke* is a duplicate; a nested, materially smaller shell is a real cutout.

Two *separate* parent-ratio caps (`vector.MAX_CUTOUT_PARENT_RATIO`, `scoring.MAX_PARENT_RATIO`)
then each independently rejected that bore for being "too big for its part". **They must track
each other.**

Cut length is computed from `π·d`, `2(L+W)`, `2(L−W)+πW` — not the polygon perimeter. A CAD
circle is a many-segment polyline and a snapped raster circle is a 16-gon; both under-measure
a true circumference.

### 7. A notch is a bite out of the outline — and its mouth is never cut

A notch is open to the part's edge, so it is invisible to everything above: it is never an
enclosed loop or a planar face. `vector._notch_candidates` reads it off the part outline's
**concavities** instead — convex hull minus outline — and keeps a piece only if it looks like
a manufactured cut. Two gates, and on the sample set they separate cleanly by two orders of
magnitude:

- **Shape**: the piece must fit an ideal rectangle/obround (same `SLOT_FIT_THRESHOLD`, 0.90).
  The flange's real 340×100 notch fits at **0.97**; gear tooth gaps fit at ~0.6 and A (3)'s
  tapered beam ends — big concavities, but the part's own shape — are triangles at **0.50**.
- **Size**: ≥1% of its part (`NOTCH_MIN_HOST_FRAC`). The notch is 14% of the flange; a tooth
  gap is 0.2% of the gear. Kept even though shape alone suffices on these eight, because a
  hull sliver along a near-straight arc can accidentally fit a rectangle.

Hosts are found like part outlines but **planar faces are eligible** (`_notch_hosts`): the
flange's profile never closes into one CAD loop, so it exists only as a face — top-level,
big, and containing something still required, so title-block cells stay out.

**The burn length excludes the mouth.** The open side lies on the hull, not on the part; only
the detector, holding the hull, knows which side that is, so it stores the true burn in
`measured_dims` and the BOM prefers it while the geometry is unedited (`cut_hint_mm` in
`bom/shapes.py`). The flange's notch burns 540 mm, not the 880 mm perimeter of its polygon.

One honest caveat: the detector has **exactly one real positive example**. The gates are
placed by evidence from all eight drawings, but nothing has yet tested a small real notch, a
semicircular one, or a sheet whose frame face sneaks under `FRAME_AREA_RATIO` with a
title-block-shaped concavity. More labelled drawings beat more tuning, here more than
anywhere.

---

## The BOM

`app/bom/` — one row per (shape, size), with quantity and cut length. Shared by the workspace,
the hover tooltip, the cross-document roll-up and the export, so they **cannot disagree**.

**Sizes are clustered, not snapped to a grid.** The 16 bolt holes on the gasket measure
12.25–12.40 mm; a fixed 0.5 mm grid put 12.25 *exactly* on a bucket boundary, and Python's
**banker's rounding** (`round(24.5) == 24`) sent one hole to 12.0 while its fifteen siblings
went to 12.5. **One hole type, split across two rows, by a rounding rule.** Any fixed grid has
boundaries and something eventually lands on one. Sizes now cluster against each other with a
sorted single-linkage sweep.

**Junk is separated from the work order.** Rows where nothing clears the finalize threshold are
marked `needs_review`, sorted below a divider, dimmed, and **excluded from the totals** — so
detector noise cannot inflate a cut length. They are never *hidden*: a missed hole costs a
part.

---

## The VLM: wired in, and it does not work

Maoz asked why the LLM hadn't deleted the junk. **Answer: it had never run.** It is off by
default, and escalation only ever consulted it about candidates scoring *below* 0.65. The
false positives scored **0.94–0.98**. The model was only asked about doubt, and the system has
none about its own garbage.

So `app/vlm/verify.py` adds the other direction — a **veto pass over everything that would be
auto-approved**. One call per distinct shape+size, not per cutout: 16 identical bolt holes are
one question; A (4)'s 293 holes are one question.

**Then I tested it against real Ollama and it failed.** With clean crops and correct grouping,
`qwen3.5:9b` **vetoed the real Ø605 bore at confidence 1.0**, twice. It also returned values
outside its own enum. It *did* correctly veto the three title-block symbols — but a tool that
erases the main bore of a part to catch three symbols does not get a delete key.

**Guard rails, each earned by watching it go wrong:**

- **A veto FLAGS, it does not delete.** Vetoed cutouts drop to 0.50 — below finalize, so
  nothing wrong is auto-approved, but visible and one click from restoration.
- **A confirmation moves NOTHING.** Averaging the model's confidence in would demote a real
  hole for the crime of the model being only 60% sure of something it got right.
- **A verdict only speaks for shapes that are really the same.** My first cut reused the BOM's
  0.5 mm display key and swept the title block's projection symbol (Ø2.62) into the same bucket
  as the 16 real Ø2.47 bolt holes, made the *symbol* the group's representative, and let one
  correct verdict veto all 16 real holes. **The model was right; my grouping was the footgun.**
- **The crop is never the whole page.** Maoz predicted this. A margin proportional to the shape
  meant the Ø605 bore — most of the sheet already — was sent with the entire drawing.

**Standing rule: geometry measures, the model judges, the model never touches a number.**

My honest read: **do not lean on the LLM for detection.** It is slow (~7 s/call), unreliable in
exactly the cases that matter, and its errors are invisible in a way threshold bugs are not.
The deterministic pipeline now gets 100% precision without it.

---

## Traps and things NOT to rebuild

### The crop trap (live, unfixed — Maoz's call)

**The crop tool destroys the scale.** It exists to cut away the title block and margins — which
is exactly where the printed scale and the dimension lines live. Cropping Doc_HK3573 turns a
confident **1:5** into an unverified **1:16.81**; A (3) is left with "nothing to measure a scale
from".

Maoz's decision: he simply will not crop. **The next operator will** — the UI invites it. The
original sheet survives at `originals_dir/{sha}.pdf`, so resolving the scale from *that* while
extracting geometry from the cropped region is the real fix (~15 lines). Noted in
`extraction/service.py` where the bug lives.

### Tried, measured, rejected

- **Forking the pipeline "simple vs complex"** (Maoz's suggestion). The real split is which
  drafting **convention** a sheet uses, and A (3)/A (4) share theirs with the **washer and the
  gasket** — two of the "complex" drawings. A fork would silently mis-route them. One pipeline
  that detects the convention per page already gets 100/100.
- **Part-outline gating without both conditions** — see §2. Cost 7% recall.
- **Comparing areas instead of shapes** for slot classification → 47 slots instead of 5.
- **Trusting the printed title-block scale.** It lies on at least three of eight drawings.
- **Splitting ink by width when colour "works"** — see the mistake below.

### The mistake that cost a cycle

Doc_HK3573 has **exactly one** coloured stroke on the sheet (a highlight box). An early version
of the "is this page colour-coded?" test let that single path convince it the page was
colour-coded, silently disabling the width fallback. I then swept the width threshold across
four values, got four identical results, and told Maoz that width separation "did not help" —
and wrote a comment telling the next person not to try it. **It was never switched on.**

> **If a threshold sweep gives identical results at every setting, the knob is not connected.**

---

## Open, in order of value

1. **Grow the fixture set to ~20 real drawings** with confirmed answers. 100/100 on eight
   samples is one new customer away from being wrong. **The cheapest and most informative thing
   on this list** — and the notch detector (§7) has exactly one real positive example.
2. **The crop trap** (above). ~15 lines.
3. **A (3)'s dimension-line measurement runs a few % long** — its labels imply 7.23/7.53/7.66
   where the truth is 7.75. The resolver correctly refuses to be confident and asks the
   operator. Root cause unknown (probably arrowhead overshoot). **Do not tune against A (3)
   until it is understood.**
4. **DXF export.** `export.py` even comments about "DXF consumers" but emits JSON only — the
   actual handoff to nesting/CNC, and the product's missing last mile. *(If DXF ever becomes
   available as an* input *for some customers, that path is exact — a DXF carries geometry and
   layers outright, no inference. PDF is a lossy picture of one.)*
5. **A document is not marked stale after a pipeline change.** Maoz was looking at 112
   candidates from a job run before the fixes; a re-run gives 17. Nothing tells you to re-run.
6. **No WS reconnect / polling fallback.** A dropped socket disables "Run extraction" forever.
   `GET /api/jobs/{id}` exists and is never called.
7. **Finalize is a permanent lock.** No unlock endpoint; the only escape is deleting the doc.

---

## Environment (Windows, and it bites)

`uv` is **not on PATH**; the venv lives **outside OneDrive**:

```bash
cd server
export UV_PROJECT_ENVIRONMENT="$USERPROFILE/.venvs/steelOptimaV2"
export PATH="$LOCALAPPDATA/Microsoft/WinGet/Packages/astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe:$PATH"
uv run uvicorn app.main:app --port 8000     # tests: uv run pytest -q

cd client && npm run dev                    # :5173, proxies /api + /ws to :8000
```

- **The project path contains Hebrew characters.** Use `pathlib`. `cv2.imread` fails on
  non-ASCII Windows paths — use `np.fromfile` + `cv2.imdecode`.
- **`pkill` does not work.** Kill the server by PID from `netstat -ano | grep :8000`.
- Data lives in `~/.steeloptima/data` (outside the repo).
- **Schema changes no longer wipe the DB** — `app/db/migrate.py` adds missing columns at
  startup. It only ever ADDs; the day something needs dropping is the day to adopt Alembic.

### Coordinate-space landmines

- `page.get_drawings()` returns **unrotated** coords — apply `page.rotation_matrix` to match
  render/text space.
- Renders embed their DPI, so **fitz reopens them in POINTS, not pixels**: clip rects are
  `px · 72 / dpi`.
- Raster candidates are built in pixel space, deskew-inverted, then divided by the page's
  **effective** DPI (from `renderer.render_page`) — never assume 300.
- Services must read `db_session.SessionLocal` **lazily**; the tests rebind it.

---

## How Maoz wants to be worked with

In `CLAUDE.md`, and he means it:

- **Blunt over nice.** Say when the code is bad, when an instruction is wrong, when you don't
  know. Pushing back has repeatedly been the right call — **and he has corrected *me* on ground
  truth more than once.** When he says a number looks wrong, look; he has been right every
  time.
- **No permission asks.** `bypassPermissions` is set for this project.
- **Commit and push at the end of every session.** Stage narrowly — he often has work in flight
  in the same tree (I swept his material-table pipeline into one of my commits by accident).
- **Smart lazy developer** — the [ponytail](https://github.com/DietrichGebert/ponytail) plugin
  is enabled. Reuse before building; smallest change that works; laziness in output, not in
  rigor.
