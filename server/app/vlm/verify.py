"""Ask the VLM about the candidates the CV pipeline is CONFIDENT about.

Escalation (escalation.py) reviews candidates scoring BELOW the threshold — the ones the
pipeline is unsure of. That is the wrong end of the range for the errors that actually
reach the operator. On Doc_HK3573 the surviving false positives are a GD&T feature-control
frame (⊕□1) scoring 0.98: geometry sees a circle and a square, because a circle and a
square is exactly what they are. No rule will ever separate a drafting symbol from a hole
by shape, and the VLM was never even asked, because it is only consulted about doubt.

So this runs the other way round: everything that would be auto-approved gets shown to the
model, which may VETO it. It cannot promote, it cannot rename, and it never touches a
dimension — every measurement still comes from exact geometry.

Cost is kept sane by verifying one representative per BOM GROUP rather than per cutout.
Sixteen identical Ø12.5 bolt holes are one question, not sixteen; A (4)'s 293 holes are
one question. Doc_HK3573 goes from 20 calls to 5.
"""

from pathlib import Path

import cv2
import fitz
import numpy as np

from app.bom.shapes import dims_key, shape_metrics
from app.config import settings
from app.db.models import Cutout, VlmCall
from app.extraction.vector import Candidate
from app.fusion.engine import fuse_verification
from app.telemetry import tracker
from app.vlm.client import OllamaVlmClient
from app.vlm.prompts import VERIFY_CROP_PROMPT

# Whether a circle is a hole depends on whether it sits in the metal or in the margin, so
# the crop has to show the surroundings — more than escalation's tight 25%.
VERIFY_MARGIN_RATIO = 0.6
MIN_VERIFY_MARGIN_PT = 30.0
# ...but a margin proportional to the shape means a BIG shape gets a huge margin. The
# Ø605 bore is most of the sheet already, and a 1.5x margin handed the model the entire
# drawing — title block, revision table and all. It duly got confused and vetoed the one
# real bore. Cap the margin absolutely: context, not the whole page.
MAX_VERIFY_MARGIN_PT = 90.0
TARGET_VERIFY_PX = 640
MAX_VERIFY_ZOOM = 6.0
OUTLINE_BGR = (0, 0, 255)  # red, in OpenCV's channel order
OUTLINE_PX = 2
# a verdict may only speak for shapes this close in size — far tighter than the BOM's
# 0.5mm display grouping. See group_key.
VERIFY_SNAP_MM = 0.05


def group_key(c: Candidate) -> str:
    """Candidates identical enough to share a verdict.

    Deliberately NOT the BOM's display key, which snaps sizes to 0.5mm so that
    measurement noise does not split one hole type across a dozen rows. That tolerance is
    right for a table and catastrophic here: on Doc_HK3573 it swept the title block's
    "First Angle Projection" symbol (Ø2.62mm) into the same bucket as the 16 real Ø2.47mm
    bolt holes, and made the symbol the group's representative. The model — correctly —
    said the projection symbol was not a cutout, and that one verdict then vetoed all 16
    real holes.

    A verdict may only speak for shapes that are genuinely the same one.
    """
    m = shape_metrics(c.polygon, c.kind)
    sizes = "x".join(f"{round(v / VERIFY_SNAP_MM) * VERIFY_SNAP_MM:.2f}" for v in m["dims"].values())
    return f"{m['shape']}|{sizes}"


def crop_with_outline(
    render_path: Path, dpi: int, polygon, out_path: Path
) -> bytes:
    """A generously-margined crop with the candidate outlined in red.

    The outline matters: with this much surrounding context the model would otherwise have
    no idea which of several shapes it is being asked about.
    """
    x0, y0, x1, y1 = polygon.bounds
    mx = min(
        max((x1 - x0) * VERIFY_MARGIN_RATIO, MIN_VERIFY_MARGIN_PT), MAX_VERIFY_MARGIN_PT
    )
    my = min(
        max((y1 - y0) * VERIFY_MARGIN_RATIO, MIN_VERIFY_MARGIN_PT), MAX_VERIFY_MARGIN_PT
    )
    clip = fitz.Rect(x0 - mx, y0 - my, x1 + mx, y1 + my)

    px_per_pt = dpi / 72
    with fitz.open(render_path) as doc:
        page = doc[0]
        clip = clip & page.rect
        # the render PNG embeds its DPI, so fitz reopens it in POINT units
        native_px = max(clip.width * px_per_pt, 1.0)
        upscale = min(MAX_VERIFY_ZOOM, max(1.0, TARGET_VERIFY_PX / native_px))
        zoom = px_per_pt * upscale
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip)

    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR if pix.n == 3 else cv2.COLOR_RGBA2BGR)

    # the render is a PNG, so the outline is drawn in pixel space rather than by fitz
    pts = np.array(
        [
            [(x - clip.x0) * zoom, (y - clip.y0) * zoom]
            for x, y in polygon.exterior.coords
        ],
        dtype=np.int32,
    )
    cv2.polylines(img, [pts], True, OUTLINE_BGR, OUTLINE_PX, lineType=cv2.LINE_AA)

    ok, buf = cv2.imencode(".png", img)
    png = buf.tobytes() if ok else pix.tobytes("png")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(png)
    return png


def verify_page(
    db,
    job_id: int,
    page_row,
    cutout_rows: list[Cutout],
    candidates: list[Candidate],
    scores: list[float],
    emit,
    client: OllamaVlmClient,
) -> int:
    """Veto pass over everything that would otherwise be auto-approved."""
    groups: dict[str, list[int]] = {}
    for i, score in enumerate(scores):
        if score >= settings.finalize_threshold:
            groups.setdefault(group_key(candidates[i]), []).append(i)

    calls = 0
    for key, members in list(groups.items())[: settings.vlm_max_calls_per_page]:
        # the best-scoring member speaks for the group
        rep = max(members, key=lambda i: scores[i])
        cutout = cutout_rows[rep]
        crop_path = settings.crops_dir / f"verify_job{job_id}_cutout{cutout.id}.png"
        png = crop_with_outline(
            Path(page_row.render_path),
            page_row.render_dpi,
            candidates[rep].polygon,
            crop_path,
        )
        result = client.classify_crop(png, prompt=VERIFY_CROP_PROMPT)
        calls += 1

        db.add(
            VlmCall(
                job_id=job_id,
                cutout_id=cutout.id,
                trigger="verification",
                model=client.model,
                prompt_hash=result.prompt_hash,
                crop_path=str(crop_path),
                latency_ms=result.latency_ms,
                response_json=result.raw_response,
                ok=result.ok,
            )
        )

        rejected = False
        if result.ok:
            # one verdict, applied to every member of the group: same shape, same size,
            # same answer
            for i in members:
                row = cutout_rows[i]
                new_score = fuse_verification(scores[i], result.verdict)
                if new_score < scores[i]:
                    row.confidence = new_score
                    row.source = "fused"
                    rejected = True

        tracker.emit(
            db,
            "vlm_verified",
            entity_id=cutout.id,
            payload={
                "ok": result.ok,
                "group": key,
                "members": len(members),
                "rejected": rejected,
                "latency_ms": result.latency_ms,
            },
        )
        emit(
            {
                "type": "vlm_verify",
                "cutout_id": cutout.id,
                "group": key,
                "members": len(members),
                "ok": result.ok,
                "rejected": rejected,
                "latency_ms": result.latency_ms,
                "verdict": result.verdict.model_dump() if result.ok else None,
            }
        )

    if len(groups) > settings.vlm_max_calls_per_page:
        emit(
            {
                "type": "vlm_verify_capped",
                "verified": settings.vlm_max_calls_per_page,
                "total_groups": len(groups),
            }
        )
    return calls
