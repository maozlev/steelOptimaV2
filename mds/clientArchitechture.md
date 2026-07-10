# SteelOptima V2 — Client Architecture

Validation workspace for the extraction server: upload blueprints, run extraction jobs with live progress, review candidate cutouts on top of page renders, approve/reject/edit/add, export approved geometry, and watch telemetry-driven precision stats.

## 1. Stack

| Concern | Choice | Rationale |
|---|---|---|
| Framework | React 19 + TypeScript | Ecosystem, typed API contract mirroring server Pydantic schemas |
| Build | Vite 6 | Instant dev server, proxy to FastAPI, single-command build |
| Styling | Tailwind CSS v4 (`@tailwindcss/vite`) | Dense engineering UI fast; no component-library lock-in |
| State | React state + custom hooks (no Redux) | Single-user tool, few cross-cutting stores |
| Server sync | Thin `fetch` wrapper + native WebSocket | Endpoints are few; no query-cache library needed |
| Routing | None (view state in `App`) | Two screens only: Documents ⇄ Workspace |

Deployment: `vite build` → `client/dist`, served statically by FastAPI at `/` (single process, single port). Dev mode: Vite on :5173 proxying `/api` + `/ws` to uvicorn on :8000.

## 2. Screens & Flow

```
┌────────────── DocumentsView ──────────────┐   ┌───────────────── WorkspaceView ─────────────────┐
│ UploadDropzone (PDF → POST /documents)    │   │ Toolbar: back · doc name · page tabs ·           │
│ Document list (pages, date, cutout count) │──►│   RunJobButton (VLM toggle) · ExportButton ·      │
│ HealthBadge (app + ollama + model)        │   │   SummaryButton                                   │
└───────────────────────────────────────────┘   │ ┌───────────────────────────┬──────────────────┐ │
                                                │ │ PageViewer                │ CutoutSidebar    │ │
                                                │ │  render PNG + SVG overlay │  filter: status/ │ │
                                                │ │  zoom (wheel) / pan (drag)│  kind/source/conf│ │
                                                │ │  polygons colored by      │  sorted list →   │ │
                                                │ │  status; click = select;  │  select/zoom     │ │
                                                │ │  "add" mode = drag rect   │  CutoutDetail:   │ │
                                                │ │                           │  approve/reject/ │ │
                                                │ │ JobProgress strip (WS)    │  kind/edit-rect  │ │
                                                │ └───────────────────────────┴──────────────────┘ │
                                                └──────────────────────────────────────────────────┘
SummaryPanel (modal): GET /telemetry/summary → approve-rate by source + confidence buckets, VLM stats
```

## 3. Key Design Points

- **Overlay geometry**: SVG layer with `viewBox="0 0 width_pt height_pt"` stacked over the render `<img>` — cutout polygons stay in page-point coordinates (same space as server WKT); zoom/pan is one CSS transform on the shared container. WKT parsed client-side (simple `POLYGON ((...))` parser; server geometry is always simple polygons).
- **Status colors**: pending = amber, approved = green, rejected = red 30% opacity, edited = blue, manual = violet. Confidence shown in sidebar as a bar; `<0.65` badge marks VLM-escalated candidates.
- **Job lifecycle**: POST job → open `WS /ws/jobs/{id}` → progress strip renders `page_started/page_done/vlm_call/vlm_unavailable` events live → on `job_done`, refetch cutouts. History replay makes reconnects safe.
- **Editing**: approve/reject = one click (PATCH). Edit = redraw a rectangle over the page (replaces geometry via `edited_geometry_wkt`; server keeps the original for audit) or change kind. Manual add = same rectangle drawing, POST to `/pages/{id}/cutouts`.
- **Telemetry**: `session_id = crypto.randomUUID()` per app load; UI events (`page_viewed`, `overlay_toggled`, `export_downloaded`, …) buffered and flushed to `POST /api/telemetry/events` every 5s / on unload. Mutations are tracked server-side already.
- **Export**: fetch `/documents/{id}/export`, download as `{filename}.cutouts.json`.

## 4. Project Layout

```
client/
  index.html  vite.config.ts  tsconfig.json  package.json
  src/
    main.tsx  App.tsx  index.css
    api/
      types.ts        # DTOs mirroring server schemas
      client.ts       # fetch wrapper (upload, jobs, cutouts, export, telemetry, health)
      ws.ts           # job WebSocket hook
      wkt.ts          # POLYGON parser + rect→WKT
    telemetry.ts      # session id + batched event queue
    views/
      DocumentsView.tsx
      WorkspaceView.tsx
    components/
      UploadDropzone.tsx  HealthBadge.tsx
      PageViewer.tsx      # img + SVG overlay + zoom/pan + rect drawing
      JobProgress.tsx     # WS event strip
      CutoutSidebar.tsx   CutoutDetail.tsx
      SummaryPanel.tsx    Toolbar.tsx
```

## 5. Server Wiring

- FastAPI: CORS for `http://localhost:5173` (dev); mount `client/dist` as `StaticFiles(html=True)` at `/` when the build exists (prod/demo).
- Vite dev proxy: `/api` → `http://localhost:8000`, `/ws` → `ws://localhost:8000`.
- One demo command path: `vite build` once, then `uvicorn app.main:app` serves API + UI on :8000.
