# SteelOptima V2 — Server Architecture

Hybrid geometry extraction engine that detects and validates manufacturing cutouts from industrial blueprints (PDF) by combining a deterministic CV pipeline with conditional local VLM analysis, exposed to an interactive, telemetry-tracked validation workspace.

## 1. Stack & Deployment

| Concern | Choice | Rationale |
|---|---|---|
| Runtime | Python 3.12+ | Best CV/PDF/VLM ecosystem |
| API framework | FastAPI + Uvicorn | Async, WebSocket/SSE, Pydantic schemas shared with pipeline |
| PDF parsing/rendering | PyMuPDF (fitz) | Vector path extraction (`get_drawings()`) + high-DPI raster rendering |
| CV | OpenCV + NumPy | Contour/circle detection, morphology, deskew |
| Geometry | Shapely | Polygon validation, containment, overlap, area/centroid math |
| OCR (dimensions/labels) | RapidOCR (ONNX) | No system Tesseract install needed on Windows |
| VLM | Ollama HTTP API — `qwen2.5vl:7b` (fallback `llava`) | Local, simple model management, structured JSON output |
| DB | SQLite (WAL) + SQLAlchemy 2.0 + Alembic | Single-workstation; zero ops |
| Jobs | In-process `asyncio` queue + worker task | No Redis/Celery needed at this scale |
| File storage | Local disk under `data/` | Originals, page renders, region crops, overlays |

Deployment: single process on the engineer's workstation. Ollama runs as a separate local service. **Note:** Python is not currently installed on this machine — environment setup (Python 3.12, `uv` or venv, Ollama) is a prerequisite task.

## 2. High-Level Architecture

```
                        ┌─────────────────────────────────────────┐
 Client (validation UI) │              FastAPI app                │
 ───── REST ──────────► │  /documents  /jobs  /cutouts  /telemetry│
 ◄──── WS/SSE ───────── │  progress + live pipeline events        │
                        └───────┬─────────────────────────────────┘
                                │ enqueue
                        ┌───────▼────────┐
                        │  Job Worker    │  (asyncio task, 1 job at a time)
                        └───────┬────────┘
        ┌───────────────────────┼──────────────────────────┐
        ▼                       ▼                          ▼
┌──────────────┐      ┌──────────────────┐       ┌──────────────────┐
│ Ingestion    │      │ Deterministic    │       │ VLM Service      │
│ - classify   │ ───► │ Extraction       │ ───►  │ (conditional)    │
│   vector/    │      │ - vector path    │ esc.  │ - Ollama client  │
│   raster     │      │   pipeline       │ only  │ - region crops   │
│ - render     │      │ - raster CV      │       │ - JSON schema    │
│   pages      │      │   pipeline       │       │   outputs        │
└──────────────┘      │ - OCR dims       │       └────────┬─────────┘
                      └────────┬─────────┘                │
                               ▼                          │
                      ┌──────────────────┐                │
                      │ Fusion &         │ ◄──────────────┘
                      │ Validation Engine│  merge, dedupe, geometric rules,
                      └────────┬─────────┘  final confidence
                               ▼
                  ┌─────────────────────────┐
                  │ SQLite + file store     │ ◄── Telemetry writer (all layers)
                  └─────────────────────────┘
```

## 3. Pipeline Flow

1. **Ingest** — upload PDF → store original → per page: classify **vector** (meaningful drawing paths present) vs **raster** (scanned/image-only) → render page PNG at 300 DPI (400–600 DPI for dense drawings) for UI + CV.
2. **Deterministic extraction**
   - *Vector path*: extract paths via `get_drawings()` → normalize to page coordinate space → close/snap path segments → build Shapely polygons → classify inner rings / closed loops inside the part outline as cutout candidates (circle fit → hole; rect fit → slot/notch; else freeform).
   - *Raster path*: grayscale → deskew → adaptive threshold → morphological cleanup → `findContours` (hierarchy: children of outer part contour = cutout candidates) + `HoughCircles` for holes → polygon approximation.
   - *OCR*: RapidOCR over dimension regions; associate dimension text to nearest candidate (for cross-checking measured vs annotated size).
3. **Per-candidate confidence scoring** (0–1): geometry closure quality, fit residual (circle/rect), size plausibility vs part bounds, source (vector ≫ raster), OCR dimension agreement.
4. **Conditional VLM escalation** — only candidates below threshold, plus page-level sweep when deterministic yield is anomalously low. Never a blanket VLM pass.
5. **Fusion & validation** — merge CV + VLM detections (IoU dedupe), apply hard geometric rules (closed, non-self-intersecting, inside part outline, min area, no >X% mutual overlap), compute final confidence, persist as `Cutout` rows with `status=pending`.
6. **Interactive validation** — client fetches candidates + overlays; every accept/reject/edit/add is a `TelemetryEvent` and updates cutout status/geometry (manual edits keep original geometry for audit).

## 4. VLM Escalation Policy

| Trigger | VLM task |
|---|---|
| Candidate confidence < 0.65 | Crop region (candidate bbox + 25% margin) → "is this a manufacturing cutout? type? refine bbox" |
| Vector page yields 0 candidates but drawing density is high | Full-page sweep → list suspected cutout regions → re-run CV locally on each region |
| OCR dimension conflicts with measured geometry (>5% deviation) | Crop → read dimension annotation |
| Ambiguous overlapping candidates | Crop → disambiguate |

VLM contract: prompt templates + few-shot, response forced to a Pydantic-validated JSON schema (Ollama `format` parameter). One retry on invalid JSON, then mark `vlm_failed` and fall back to CV result flagged low-confidence. Every call logged (`VlmCall`): model, prompt hash, crop path, latency, tokens, parsed result. Timeout ~120s, calls serialized (single GPU/CPU).

## 5. Data Model (SQLite)

```
Document      id, filename, sha256, path, page_count, created_at
Page          id, document_id, index, kind(vector|raster|mixed), width_pt, height_pt,
              render_path, render_dpi
ExtractionJob id, document_id, status(queued|running|done|failed), params_json,
              started_at, finished_at, error
Cutout        id, page_id, job_id, geometry_wkt, bbox, kind(hole|slot|notch|freeform),
              source(vector|raster_cv|vlm|fused|manual),
              confidence, dimension_text, measured_dims_json,
              status(pending|approved|rejected|edited), edited_geometry_wkt, updated_at
VlmCall       id, job_id, cutout_id?, trigger, model, prompt_hash, crop_path,
              latency_ms, response_json, ok
TelemetryEvent id, ts, session_id, type(job_started|stage_done|cutout_approved|
               cutout_rejected|cutout_edited|cutout_added|vlm_called|...),
               entity_id, payload_json
```

Geometry stored as WKT in page-point coordinates; API serializes to GeoJSON with pixel coords for the active render DPI.

## 6. API Surface

```
POST   /api/documents                    upload PDF → ingest
GET    /api/documents / {id} / {id}/pages
GET    /api/pages/{id}/render            PNG (+ ?overlay=true)

POST   /api/documents/{id}/jobs          start extraction (params: dpi, thresholds, vlm on/off)
GET    /api/jobs/{id}                    status + stage summary
WS     /ws/jobs/{id}                     live events: stage, candidate found, vlm call, done

GET    /api/pages/{id}/cutouts           candidates w/ geometry, confidence, source
PATCH  /api/cutouts/{id}                 approve | reject | edit geometry
POST   /api/pages/{id}/cutouts           manual add
GET    /api/documents/{id}/export        approved cutouts → JSON/DXF-ready payload

POST   /api/telemetry/events             client-side UI events (batched)
GET    /api/telemetry/summary            precision proxy (approve rate by source/confidence bucket)
GET    /api/health                       app + ollama + model availability
```

## 7. Project Layout

```
server/
  app/
    main.py                # FastAPI app, lifespan (DB, worker, ollama check)
    config.py              # Pydantic Settings (paths, model name, thresholds, DPI)
    api/                   # routers: documents, jobs, cutouts, telemetry, health
    ws/events.py           # job event broadcaster
    workers/queue.py       # asyncio job queue + worker loop
    ingestion/             # pdf_loader, page_classifier, renderer
    extraction/
      vector.py            # get_drawings → shapely candidates
      raster.py            # OpenCV pipeline
      ocr.py               # RapidOCR + dimension association
      scoring.py           # confidence model
    vlm/
      client.py            # Ollama HTTP client (httpx)
      prompts.py           # templates + JSON schemas
      escalation.py        # trigger policy
    fusion/engine.py       # merge, dedupe, geometric rules
    db/                    # models.py, session.py, alembic/
    telemetry/tracker.py   # event write API used by all layers
    schemas/               # Pydantic DTOs (shared client contract)
  tests/                   # unit (geometry, scoring, fusion) + pipeline tests on pdfs/
  data/                    # originals/, renders/, crops/, steel_optima.db  (gitignored)
  pyproject.toml
```

## 8. Cross-Cutting Concerns

- **Telemetry is first-class**: single `tracker.emit()` used by pipeline stages, VLM client, and API mutations; summary endpoint computes source-level precision proxies (VLM vs CV approve rates) to tune escalation thresholds over time.
- **Determinism & audit**: job params snapshot stored per job; original geometry preserved on edit; VLM prompt hash + raw response stored.
- **Failure isolation**: VLM/Ollama being down degrades gracefully — pipeline completes CV-only, candidates flagged `vlm_unavailable`.
- **Windows/OneDrive caveat**: project lives in OneDrive with a Unicode (Hebrew) path — keep `data/` and the venv **outside** OneDrive sync or exclude them, and use `pathlib` everywhere (no ANSI-path assumptions).
- **Performance targets**: vector page < 2s CV-only; raster page < 5s; VLM call 5–30s each — hence strict conditional escalation.

## 9. Build Order (suggested milestones)

1. Skeleton: FastAPI app, config, DB models, upload + ingest + page render.
2. Vector extraction pipeline + scoring on `pdfs/` samples.
3. Raster CV pipeline + OCR.
4. Job worker + WS progress events.
5. Ollama VLM client, escalation policy, fusion engine.
6. Cutout CRUD + validation endpoints + telemetry.
7. Export + telemetry summary + threshold tuning.
