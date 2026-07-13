# SteelOptima V2 — state of play (2026-07-13)

Read this first. It is written for a session that has never seen the project, and it is
biased toward the things that are *not* obvious from the code: the wrong turns, the bugs
that hid behind plausible numbers, and the facts that were confirmed by a human rather
than assumed.

---

## What the system is

Upload an engineering drawing (PDF / JPEG / PNG). It finds the manufacturing cutouts —
holes, slots, notches, freeform openings — measures them in **real-world millimetres**,
and produces a bill of materials with quantities and **cut length** (the burn distance, in
metres, which is what cutting actually costs). The operator reviews the detections on a
canvas, finalizes, and exports.

**Stack.** FastAPI + SQLite + an in-process asyncio worker + WebSocket job events; React 19
+ Vite + Tailwind. Ollama (`qwen3.5:9b`) for the vision model. Single workstation, no
Redis, no Celery, no Postgres. Don't add them.

**Pipeline.** ingest → classify page (vector / raster) → render 300 DPI → optional crop →
**separate geometry ink from annotation ink** → polygonize → classify shapes → score →
**resolve the sheet scale** → VLM rescue + veto passes → human review → finalize → export.

---

## Where accuracy actually stands

**94% recall, 98% precision, per drawing.** 139 tests pass.

| Drawing | Part | Scale | True | Found | Recall |
|---|---|---|---|---|---|
| 117-626-141_1 | Flange | 1:5 | 2 | 1 | **50%** — misses the notch |
| 117-626-141_4 | Plate | 1:2 | 2 | 2 | 100% |
| 12562-3000F501023 | Plate | **2:1** | 2 | 2 | 100% |
| 333-532-294 | Washer | 1:3 | 1 | 1 | 100% |
| ASH-071222 | Gear | 1:3.5 | 1 | 1 | 100% |
| A (3) | Beam | 1:7.75 | 148 | 148 | 100% |
| A (4) | Beam | 1:1 | 293 | 293 | 100% |
| Doc_HK3573 | Gasket | 1:5 | 17 | 20 | 100% (3 false positives) |

### The most important file in the repo

`server/tests/fixtures/ground_truth.json` — the right answers, **confirmed by Maoz against
the drawings**, plus `server/tools/eval_detection.py` which scores the pipeline against it.

    uv run python tools/eval_detection.py

Without this, "accuracy" is a feeling. **Every single significant fix in this project was
found or validated by that harness, and two were reverted by it.** Run it before and after
any change to extraction.

It reports **micro** (per-cutout) and **macro** (per-drawing) separately on purpose: micro
reads ~99% only because A (4) contributes 293 identical holes and drowns out five broken
drawings. **Macro is the honest number.** I nearly reported the flattering one.

### The rule for what counts as a cutout

Confirmed by Maoz:

- enclosed holes (circle / rectangle / obround slot) → **YES**
- notches cut **into** the part, open to its edge → **YES**
- the outer profile itself, chamfers, gear teeth → **NO** (that is the part's shape)

### Error bias, also confirmed

**Never miss a real hole.** A false positive costs a click; a missed hole costs a part.
When in doubt, surface it — never silently drop it.

---

## The five ideas the whole thing rests on

### 1. Separate the ink before you polygonize anything

The original extractor polygonized *every stroke on the page* — part edges, leader lines,
arrowheads, glyph outlines — into one soup, then tried to sort cutouts back out by shape.
**That cannot work.** The `Ø` in a label "Ø290 THRU" is drawn as vector paths and *is*,
geometrically, a circle. On the gear it was auto-approved at 0.98 as a Ø3.1 hole while the
real Ø290 bore was rejected. No threshold will ever separate a glyph from a hole by shape.

But CAD exports say which ink is which, and nobody was reading it. `app/extraction/ink.py`:

- **Colour**: part geometry black; dimensions/leaders grey or olive; frame light grey.
- **Stroke width** (when the sheet is one colour): ISO draws part edges thick, dimension /
  extension / leader / centre lines thin.

Roughly half the drawings use each convention. **The convention is detected per page.**

### 2. Scale, or you ship wrong parts

Every measurement is in PAPER millimetres. On a 1:3.5 sheet the gear's Ø290 bore measures
82.9mm of paper, and the BOM reported exactly that. This is the most dangerous class of bug
in the system: it does not look broken, it just quietly produces parts of the wrong size.

`app/extraction/scale.py`. A label like "624" is not merely *near* the flange's width — it
is written **on the dimension line that spans it**, and that line is 624mm of reality long.
So `scale = label / length of the line it sits on`, which is self-checking: a sheet has one
scale and every dimension line must agree. Diameter callouts are followed **down their
leader** to the bore they name.

**The printed scale only cross-checks. It never decides, because title blocks lie:**

- ASH prints "Scale 1:3.5" on the sheet and "SCALE:1:5" in its own title block.
- The plate prints 1:2 and 1:1.

They are stale template defaults. Believing them cuts parts 40% oversize. The resolver
detects the contradiction, believes the drawing's own dimensions, and says so.

Where it cannot establish a scale it **refuses to guess**: the page is flagged, the BOM is
marked untrustworthy, the export names the unverified pages, and the operator types the
scale (`PATCH /api/pages/{id}/scale`). Five seconds beats a silently mis-cut part.

### 3. Shape is measured against ideal shapes, not against areas

`_classify` and `app/bom/shapes.py`. The DB `kind` enum stores a true rectangle and an
obround slot **both as "slot"** — they clear the same gate. A BOM must tell them apart:
different cut lengths, different tooling. So the displayed shape is derived from geometry
by IoU against the *ideal* rectangle and the *ideal* obround.

Comparing **areas** instead of shapes was my first attempt and it made the gasket report 47
slots instead of 5. An arbitrary blob can share an obround's area ratio without being one.

### 4. Overlap is not duplication

A gasket's Ø605 bore fills 78% of the Ø686 ring around it. The dedupe dropped anything
overlapping a kept shell by >40% IoU — a rule written to kill concentric countersink
strokes — so **the system deleted the central hole precisely because the part is a ring.**
Only a near-identical *restroke* is a duplicate; a nested, materially smaller shell is a
real cutout.

Two *separate* parent-ratio caps (`vector.MAX_CUTOUT_PARENT_RATIO`, `scoring.MAX_PARENT_RATIO`)
then each independently rejected that bore for being "too big for its part". They must
track each other.

### 5. Cut length comes from the ideal shape, not the polygon

A CAD circle is a many-segment polyline; a snapped raster circle is a 16-gon. Both
under-measure a true circumference. Cut length is computed from `π·d`, `2(L+W)`,
`2(L−W)+πW` as appropriate.

BOM rows **group** on a size snapped to 0.5mm (so measurement noise doesn't split one hole
type across a dozen rows) but **display the group's mean size** — a row labelled "Ø 5.2 mm"
must show a cut length of π×5.2, or the operator stops trusting the table.

---

## The VLM: it is wired in, and it does not work

Maoz asked why the LLM hadn't deleted the junk. **Answer: it had never run.** It is off by
default, and escalation only ever consulted it about candidates scoring *below* 0.65. The
false positives score 0.94–0.98. The model was only ever asked about doubt, and the system
has none about its own garbage.

So `app/vlm/verify.py` adds the other direction — a **veto pass over everything that would
be auto-approved**. One call per distinct shape+size, not per cutout: 16 identical bolt
holes are one question; A (4)'s 293 holes are one question.

**Then I tested it against real Ollama and it failed.** With clean crops and correct
grouping, `qwen3.5:9b` **vetoed the real Ø605 bore at confidence 1.0**, twice. It also
returned values outside its own enum. It *did* correctly veto the three title-block symbols
— but a tool that erases the main bore of a part to catch three symbols does not get a
delete key.

**Guard rails, each earned by watching it go wrong:**

- **A veto FLAGS, it does not delete.** Vetoed cutouts drop to 0.50 — below finalize, so
  nothing wrong is auto-approved, but visible in review and one click from restoration.
- **A confirmation moves NOTHING.** Averaging the model's confidence in would demote a real
  hole for the crime of the model being only 60% sure of something it got right
  (0.5·0.98 + 0.5·0.6 + 0.1 = 0.89, under the 0.90 threshold). The model objects; it does
  not grade.
- **A verdict only speaks for shapes that are really the same.** My first cut reused the
  BOM's 0.5mm display key and swept the title block's "First Angle Projection" symbol
  (Ø2.62) into the same bucket as the 16 real Ø2.47 bolt holes, made the *symbol* the
  group's representative, and let one correct verdict veto all 16 real holes. **The model
  was right; my grouping was the footgun.**
- **The crop is never the whole page.** Maoz predicted this. A margin proportional to the
  shape meant the Ø605 bore — most of the sheet already — was sent with the entire drawing,
  title block and all. Margins are capped absolutely now.

**Standing rule: geometry measures, the model judges. The model never touches a number.**

My honest read: **do not lean on the LLM to fix detection.** It is slow (~7s/call),
unreliable in exactly the cases that matter, and its errors are invisible in a way that
threshold bugs are not.

---

## Things tried and REJECTED — do not rebuild these

- **Forking the pipeline** into "simple" (A (3)/A (4)) and "complex" drawings. The real
  split is *which drafting convention was used*, not part complexity — and A (3)/A (4)
  share their convention with the washer and the gasket, two of the "complex" ones. A fork
  would silently mis-route them. One pipeline that *detects the convention per page* already
  gets 94/98.
- **Part-outline gating** ("a cutout must lie inside a part outline"). The title-block
  symbols are themselves closed loops with nothing around them, so they qualify as parts
  and admit themselves; and on 12562 it dropped a real slot, taking recall 93% → 86%.
  Documented in `vector.py`.
- **Comparing areas instead of shapes** for slot classification → gasket reported 47 slots
  instead of 5.
- **Trusting the title-block scale.** It lies on at least three of eight drawings.

### And one mistake worth remembering

I told Maoz that width-based ink separation "didn't help" and wrote a comment telling the
next person not to try it. **The feature was never switched on** — a single stray coloured
stroke on the sheet made my guard think the page was colour-coded. I measured a disabled
feature across four thresholds, got four identical results, and confidently reported it did
nothing. When a sweep produces *identical* numbers at every setting, the knob isn't
connected.

---

## Open items, in order of value

1. **The flange's notch.** A whole class of feature — cuts open to the part's edge — is
   invisible today. Needs concavity analysis of the part outline, not enclosed-loop
   detection. This is the only genuine recall gap left.
2. **A (3)'s dimension-line measurement is a few percent long.** Its three big labels imply
   7.23 / 7.53 / 7.66 where the truth is 7.75, so the resolver (correctly) refuses to be
   confident and asks the operator. Root cause not yet understood — probably arrowhead
   overshoot on the measured line. **Do not tune against A (3) until it is.**
3. **Doc_HK3573's three title-block artifacts** (a Ø19.9 circle, a Ø13.1 circle, a 9.8×8.7
   rectangle — the "First Angle Projection" symbol and the ⊕□1 frame). Drawn in thick ink,
   geometrically genuine circles and a square. Three clicks. Lowest value.
4. **DXF export.** `export.py` comments about "DXF consumers" but emits JSON only. This is
   the actual handoff to a nesting/CNC tool — arguably the product's missing last mile.
5. **No WS reconnect / polling fallback.** If the socket drops, "Run extraction" stays
   disabled forever. `GET /api/jobs/{id}` exists and is never called.

---

## Environment (Windows, and it bites)

- **Python 3.12 via `uv`**, which is **not on PATH**. The venv lives **outside OneDrive**.

      cd server
      export UV_PROJECT_ENVIRONMENT="$USERPROFILE/.venvs/steelOptimaV2"
      export PATH="$LOCALAPPDATA/Microsoft/WinGet/Packages/astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe:$PATH"
      uv run uvicorn app.main:app --port 8000     # tests: uv run pytest -q

      cd client && npm run dev                    # :5173, proxies /api + /ws to :8000

- **The project path contains Hebrew characters.** Use `pathlib`. `cv2.imread` fails on
  non-ASCII Windows paths — use `np.fromfile` + `cv2.imdecode`.
- `pkill` does not work. Kill the server by PID from `netstat -ano | grep :8000`.
- Data lives in `~/.steeloptima/data` (outside the repo).
- **Schema changes no longer wipe the DB**: `app/db/migrate.py` adds missing columns at
  startup. It only ever ADDs. The day something needs dropping is the day to adopt Alembic.

### Coordinate-space landmines

- `page.get_drawings()` returns **unrotated** coords. Apply `page.rotation_matrix` to match
  render/text space.
- Renders embed their DPI, so **fitz reopens them in POINTS, not pixels**. Clip rects are
  `px · 72 / dpi`.
- Raster candidates are built in pixel space, deskew-inverted, then divided by the page's
  **effective** DPI (from `renderer.render_page`) — never assume 300.
- Services must read `db_session.SessionLocal` **lazily**; the tests rebind it.

---

## How Maoz wants to be worked with

In `CLAUDE.md`, and it is meant:

- **Blunt over nice.** Say when the code is bad, when an instruction is wrong, when you
  don't know. He asked for the hard truth and he means it — pushing back has repeatedly been
  the right call, and *he* has corrected *me* on ground truth more than once.
- **No permission asks.** `bypassPermissions` is set for this project.
- **Commit and push at the end of every session.**
- **Smart lazy developer** — the [ponytail](https://github.com/DietrichGebert/ponytail)
  plugin is enabled. Reuse before building; smallest change that works; laziness in output,
  not in rigor.
