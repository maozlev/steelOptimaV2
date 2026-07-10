import json
from pathlib import Path

import fitz

from app.db.models import Cutout

KIND_COLORS = {
    "hole": (255, 0, 0),
    "slot": (0, 128, 255),
    "notch": (255, 128, 0),
    "freeform": (200, 0, 200),
}
LINE_W = 3


def _draw_box(pix: fitz.Pixmap, x0: int, y0: int, x1: int, y1: int, color) -> None:
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(pix.width, x1), min(pix.height, y1)
    if x1 <= x0 or y1 <= y0:
        return
    pix.set_rect(fitz.IRect(x0, y0, x1, min(y0 + LINE_W, y1)), color)
    pix.set_rect(fitz.IRect(x0, max(y1 - LINE_W, y0), x1, y1), color)
    pix.set_rect(fitz.IRect(x0, y0, min(x0 + LINE_W, x1), y1), color)
    pix.set_rect(fitz.IRect(max(x1 - LINE_W, x0), y0, x1, y1), color)


def render_overlay(render_path: Path, dpi: int, cutouts: list[Cutout]) -> bytes:
    pix = fitz.Pixmap(str(render_path))
    scale = dpi / 72
    for c in cutouts:
        x0, y0, x1, y1 = json.loads(c.bbox)
        color = KIND_COLORS.get(c.kind, (255, 0, 0))
        _draw_box(
            pix,
            int(x0 * scale) - LINE_W,
            int(y0 * scale) - LINE_W,
            int(x1 * scale) + LINE_W,
            int(y1 * scale) + LINE_W,
            color,
        )
    return pix.tobytes("png")
