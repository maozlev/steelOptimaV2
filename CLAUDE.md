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
`listDocumentCutouts`, a server-side overlay renderer, and a `notch` enum that nothing calls —
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

## Landmines — read before touching the pipeline

- **Hebrew chars in the project path.** Use `pathlib`. `cv2.imread` fails on non-ASCII Windows
  paths — use `np.fromfile` + `cv2.imdecode`.
- **No Alembic.** Any schema change means wiping the dev DB at `~/.steeloptima/data`.
- **Coordinate spaces bite.** `page.get_drawings()` returns *unrotated* coords — apply
  `page.rotation_matrix` to match render/text space. Renders embed their DPI, so fitz reopens
  them in *points*, not pixels: clip rects are `px * 72 / dpi`.
- **Raster candidates** are built in pixel space, deskew-inverted, then divided by the page's
  *effective* DPI (from `renderer.render_page`) — never assume 300.
- **Services must read `db_session.SessionLocal` lazily** — the tests rebind it.
- **Extraction branches only on `kind == "raster"`.** `mixed` and empty pages take the vector
  path, so a scanned underlay on a mixed page is silently never CV-processed.

## Known design flaws (not yet fixed — deliberate backlog)

1. **The VLM's work is always thrown away.** Escalation fires below 0.65; fusion is
   `0.5*cv + 0.5*vlm (+0.1 if kinds agree)`; finalize auto-approves at 0.90. A 0.64 candidate
   therefore needs `vlm_confidence >= 0.96` just to survive. In practice every VLM-reviewed
   cutout is auto-rejected unless a human intervenes — we pay 6–8s per call for nothing.
2. **The workspace BOM is per-page, not per-document.** `WorkspaceView` fetches
   `listCutouts(page.id)`, so on a multi-page drawing the BOM panel and the finalize count only
   reflect the current page. `listDocumentCutouts` already exists and is unused here.
3. **No DXF export.** `export.py` even comments about "DXF consumers" but emits JSON only. This
   is the actual handoff to nesting/CNC — the product's missing last mile.
4. **Finalize is a permanent lock.** No unlock endpoint; the only escape is deleting the doc.
5. **No WS reconnect or polling fallback.** If the socket drops, `jobRunning` sticks true and
   "Run extraction" stays disabled forever. `GET /api/jobs/{id}` exists and is never called.
