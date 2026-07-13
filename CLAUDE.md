# SteelOptima V2

Blueprint-to-BOM engine. Ingests engineering drawings (PDF/JPEG/PNG), detects manufacturing
cutouts (hole / slot / notch / freeform) with a deterministic CV pipeline, escalates only
low-confidence candidates to a local Ollama VLM, and hands the operator an interactive
workspace to review and finalize a bill of materials.

## How Maoz wants me to work

**Don't be nice. Give the hard truth.** If the code is bad, say it's bad. If an instruction is
wrong or impossible, say so instead of quietly working around it. If I don't know, say I don't
know. No hedging, no flattery, no "great question". Disagreement is more useful than agreement.

**Don't ask for permission.** Permission mode is set to `bypassPermissions` in
`.claude/settings.local.json`. Just do the work. Still confirm before genuinely destructive or
outward-facing actions (history rewrites, force-push, deleting data, anything public).

**Be a smart lazy developer.** The [ponytail](https://github.com/DietrichGebert/ponytail) plugin
is enabled for this repo (`.claude/settings.json`) and enforces the decision ladder: does it need
to exist → already in the codebase → stdlib → native platform → existing dependency → one-liner →
only then write something new. Follow it. This codebase in particular already has
`listDocumentCutouts` and a server-side overlay renderer that are easy to miss —
check before you build. Laziness is about *output*, not *rigor*: still read the code, still run
the tests.

**End of every session: commit and push.** Real commit message describing what actually changed
and why. `git push` to `origin main`. Don't leave work uncommitted.

## Running it

Server (port 8000) — venv lives outside OneDrive, and `uv` is not on PATH:

```bash
cd server
export UV_PROJECT_ENVIRONMENT="$USERPROFILE/.venvs/steelOptimaV2"
export PATH="$LOCALAPPDATA/Microsoft/WinGet/Packages/astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe:$PATH"
uv run uvicorn app.main:app --port 8000    # tests: uv run pytest -q
```

Client (port 5173, proxies `/api` + `/ws` → :8000):

```bash
cd client && npm run dev
```

## Before touching extraction: run the eval harness

`mds/HANDOFF.md` is the full brief — read it first.

```bash
cd server && uv run python tools/eval_detection.py
```

It scores the pipeline against `tests/fixtures/ground_truth.json` — the right answers,
confirmed by Maoz against the actual drawings. **Currently 100% recall / 100% precision per
drawing.** Every significant fix in this project was found or validated by that harness, and
*three were reverted by it*. Run it before and after any change. Trust the **macro**
(per-drawing) number: micro flatters, because A (4) contributes 293 identical holes.

Also `uv run python tools/inspect_ink.py` — run this FIRST on any drawing that misbehaves. It
says how the page was read (colour convention / stroke width / fail-safe) and why. A wrong
convention decision looks like a detection bug everywhere downstream.

**Ground truth has been wrong twice, and Maoz caught both.** When he says a number looks
wrong, go and look — he has been right every time. The limiting factor on this project is
labelled drawings, not algorithms.

Rules Maoz confirmed: cutouts are enclosed holes and notches cut *into* the part — not the
outer profile, chamfers or gear teeth. And **never miss a real hole**: a false positive costs
a click, a missed hole costs a part.

## Landmines — read before touching the pipeline

- **Hebrew chars in the project path.** Use `pathlib`. `cv2.imread` fails on non-ASCII Windows
  paths — use `np.fromfile` + `cv2.imdecode`.
- **Title blocks lie about scale.** Three of eight drawings print one scale on the sheet and a
  different one in the block. The printed scale only ever cross-checks; the drawing's own
  dimension lines decide. See `extraction/scale.py`.
- **Ink must be separated before polygonizing.** A `Ø` glyph *is* a circle — shape can never
  tell it from a hole. CAD marks layers by stroke colour or stroke width; `extraction/ink.py`
  detects which convention a page uses. Roughly half the drawings use each.
- **`pkill` doesn't work here.** Kill the server by PID from `netstat -ano | grep :8000`.
- Schema changes no longer wipe the DB — `db/migrate.py` adds missing columns at startup. It
  only ever ADDs; the day something needs dropping is the day to adopt Alembic.
- **Coordinate spaces bite.** `page.get_drawings()` returns *unrotated* coords — apply
  `page.rotation_matrix` to match render/text space. Renders embed their DPI, so fitz reopens
  them in *points*, not pixels: clip rects are `px * 72 / dpi`.
- **Raster candidates** are built in pixel space, deskew-inverted, then divided by the page's
  *effective* DPI (from `renderer.render_page`) — never assume 300.
- **Services must read `db_session.SessionLocal` lazily** — the tests rebind it.
- **Extraction branches only on `kind == "raster"`.** `mixed` and empty pages take the vector
  path, so a scanned underlay on a mixed page is silently never CV-processed.

## The VLM does not work — do not lean on it

Tested live: `qwen3.5:9b` **vetoed a real Ø605 bore at confidence 1.0**. It correctly caught
three title-block symbols, but a tool that erases the main bore of a part to catch three
symbols does not get a delete key.

**Geometry measures. The model judges. The model never touches a number.** A veto *flags*
(drops to 0.50, visible, one click to restore) — it never deletes. A confirmation moves
nothing. See `vlm/verify.py`.

## Live trap: cropping destroys the scale

The crop tool removes the title block and margins — **exactly where the printed scale and the
dimension lines live**. Cropping Doc_HK3573 turns a confident 1:5 into an unverified 1:16.81.
Maoz's call is that he simply won't crop; the next operator will. The original sheet survives
at `originals_dir/{sha}.pdf`, so resolving the scale from *that* is the real fix (~15 lines).
Unfixed, noted in `extraction/service.py`.

## Do NOT rebuild these (tried, measured, rejected)

- **Forking the pipeline "simple vs complex".** The real split is which drafting *convention*
  a sheet uses, and A (3)/A (4) share theirs with the washer and the gasket. A fork would
  silently mis-route them. One pipeline that detects the convention per page gets 100/100.
- **Part-outline gating without BOTH conditions.** A part outline must be *big* AND *contain
  something*. Missing the size test does nothing at all (the symbols are top-level loops and
  admit themselves); missing "contains something" costs 7% recall (12562's octagon is only a
  planar face, so its own slots got declared "parts").
- **Comparing areas instead of shapes** for slot classification → the gasket reported 47 slots
  instead of 5.
- **Trusting the printed title-block scale.** It lies on three of eight drawings.

If a threshold sweep gives *identical* results at every setting, the knob isn't connected. I
lost a cycle to that.

## Open backlog, in order of value

1. **Grow the fixture set to ~20 real drawings** with confirmed answers. 100/100 on eight
   samples is one new customer away from being wrong. Cheapest and most informative item here.
   The notch detector in particular has exactly ONE real positive example (the flange).
2. **The crop trap** (above). ~15 lines.
3. **A (3)'s dimension-line measurement runs a few % long** (its labels imply 7.23/7.53/7.66;
   the truth is 7.75). The resolver correctly refuses to be confident and asks the operator.
   Root cause unknown — don't tune against A (3) until it is.
4. **No DXF export.** `export.py` even comments about "DXF consumers" but emits JSON only —
   the actual handoff to nesting/CNC, and the product's missing last mile.
5. **Nothing marks a document stale** after a pipeline change. Maoz was once looking at 112
   candidates from a job run before the fixes; a re-run gave 17.
6. **No WS reconnect or polling fallback.** If the socket drops, `jobRunning` sticks true and
   "Run extraction" stays disabled forever. `GET /api/jobs/{id}` exists and is never called.
7. **Finalize is a permanent lock.** No unlock endpoint; the only escape is deleting the doc.
