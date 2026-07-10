import json
from pathlib import Path

import fitz

from app.config import settings
from app.db.models import Cutout, VlmCall
from app.extraction.vector import Candidate
from app.fusion.engine import fuse_verdict
from app.telemetry import tracker
from app.vlm.client import OllamaVlmClient

CROP_MARGIN_RATIO = 0.25
MIN_CROP_MARGIN_PT = 15.0
# small crops are upscaled so thin contour lines survive the VLM's input resize
TARGET_CROP_PX = 400
MAX_CROP_ZOOM = 4.0


def select_for_escalation(scores: list[float]) -> list[int]:
    """Sub-threshold candidates, most promising first, capped per page."""
    idxs = [i for i, s in enumerate(scores) if s < settings.escalation_threshold]
    idxs.sort(key=lambda i: -scores[i])
    return idxs[: settings.vlm_max_calls_per_page]


def crop_candidate(
    render_path: Path, dpi: int, bbox_pt: tuple, out_path: Path
) -> bytes:
    # the render PNG embeds its DPI, so fitz reopens it in point units and
    # clip coords are points; zoom is then px-per-pt times an upscale factor
    x0, y0, x1, y1 = bbox_pt
    mx = max((x1 - x0) * CROP_MARGIN_RATIO, MIN_CROP_MARGIN_PT)
    my = max((y1 - y0) * CROP_MARGIN_RATIO, MIN_CROP_MARGIN_PT)
    clip = fitz.Rect(x0 - mx, y0 - my, x1 + mx, y1 + my)
    px_per_pt = dpi / 72
    with fitz.open(render_path) as doc:
        page = doc[0]
        clip = clip & page.rect
        native_px = max(clip.width * px_per_pt, 1.0)
        upscale = min(MAX_CROP_ZOOM, max(1.0, TARGET_CROP_PX / native_px))
        zoom = px_per_pt * upscale
        png = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip).tobytes(
            "png"
        )
    out_path.write_bytes(png)
    return png


def escalate_page(
    db,
    job_id: int,
    page_row,
    cutout_rows: list[Cutout],
    candidates: list[Candidate],
    scores: list[float],
    emit,
    client: OllamaVlmClient,
) -> int:
    """Trigger 1 of the escalation policy: sub-threshold candidates get a
    cropped-region VLM review; the verdict is fused into the stored cutout."""
    calls = 0
    for i in select_for_escalation(scores):
        cutout = cutout_rows[i]
        crop_path = settings.crops_dir / f"job{job_id}_cutout{cutout.id}.png"
        png = crop_candidate(
            Path(page_row.render_path),
            page_row.render_dpi,
            candidates[i].polygon.bounds,
            crop_path,
        )
        result = client.classify_crop(png)
        calls += 1
        db.add(
            VlmCall(
                job_id=job_id,
                cutout_id=cutout.id,
                trigger="low_confidence",
                model=client.model,
                prompt_hash=result.prompt_hash,
                crop_path=str(crop_path),
                latency_ms=result.latency_ms,
                response_json=result.raw_response,
                ok=result.ok,
            )
        )
        if result.ok:
            kind, confidence = fuse_verdict(
                cutout.kind, scores[i], result.verdict
            )
            cutout.kind = kind
            cutout.confidence = confidence
            cutout.source = "fused"
            cutout.measured_dims_json = cutout.measured_dims_json or json.dumps(
                candidates[i].measured_dims
            )
        tracker.emit(
            db,
            "vlm_called",
            entity_id=cutout.id,
            payload={"ok": result.ok, "latency_ms": result.latency_ms},
        )
        emit(
            {
                "type": "vlm_call",
                "cutout_id": cutout.id,
                "ok": result.ok,
                "latency_ms": result.latency_ms,
                "verdict": result.verdict.model_dump() if result.ok else None,
            }
        )
    return calls
