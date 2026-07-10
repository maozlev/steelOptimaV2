import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from shapely.geometry import Point

from app.extraction.vector import Candidate

TILE_PX = 1280
TILE_OVERLAP_PX = 64
REGION_MARGIN_PT = 40.0
MIN_WORD_SCORE = 0.5
WORD_DEDUPE_DIST_PT = 5.0
MAX_ASSOC_DIST_PT = 150.0
# words bigger than this fraction of the candidate box are the shape itself
# misread as a glyph (a drilled hole OCRs as the letter "O"), not a label
TEXT_IN_BOX_MAX_RATIO = 0.5

DIA_RE = re.compile(r"[Øø⌀∅¢](?:\s*)(\d+(?:[.,]\d+)?)")
SLOT_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*[xX×]\s*(\d+(?:[.,]\d+)?)")


@dataclass
class OcrWord:
    text: str
    bbox: tuple[float, float, float, float]  # page points

    @property
    def center(self) -> tuple[float, float]:
        return ((self.bbox[0] + self.bbox[2]) / 2, (self.bbox[1] + self.bbox[3]) / 2)


def parse_dimension(text: str) -> dict | None:
    m = DIA_RE.search(text)
    if m:
        return {"diameter_mm": float(m.group(1).replace(",", "."))}
    m = SLOT_RE.search(text)
    if m:
        a = float(m.group(1).replace(",", "."))
        b = float(m.group(2).replace(",", "."))
        return {"length_mm": max(a, b), "width_mm": min(a, b)}
    return None


@lru_cache(maxsize=1)
def _engine():
    from rapidocr_onnxruntime import RapidOCR

    return RapidOCR()


def _load_gray(render_path: Path) -> np.ndarray:
    data = np.fromfile(str(render_path), dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise ValueError(f"cannot decode image: {render_path}")
    return img


def _tiles_for_regions(
    img_w: int, img_h: int, boxes_px: list[tuple[float, float, float, float]]
) -> list[tuple[int, int]]:
    step = TILE_PX - TILE_OVERLAP_PX
    tiles = []
    for ty in range(0, max(img_h - TILE_OVERLAP_PX, 1), step):
        for tx in range(0, max(img_w - TILE_OVERLAP_PX, 1), step):
            tx1, ty1 = tx + TILE_PX, ty + TILE_PX
            if any(
                b[0] < tx1 and b[2] > tx and b[1] < ty1 and b[3] > ty
                for b in boxes_px
            ):
                tiles.append((tx, ty))
    return tiles


def ocr_words_near(
    render_path: Path,
    dpi: int,
    regions_pt: list[tuple[float, float, float, float]],
) -> list[OcrWord]:
    """OCR only the tiles around candidate regions — full-page OCR at blueprint
    resolution is both slow and lossy (detector downscales huge images)."""
    if not regions_pt:
        return []
    img = _load_gray(render_path)
    h, w = img.shape
    s = dpi / 72
    boxes_px = [
        (
            (b[0] - REGION_MARGIN_PT) * s,
            (b[1] - REGION_MARGIN_PT) * s,
            (b[2] + REGION_MARGIN_PT) * s,
            (b[3] + REGION_MARGIN_PT) * s,
        )
        for b in regions_pt
    ]

    words: list[OcrWord] = []
    for tx, ty in _tiles_for_regions(w, h, boxes_px):
        tile = img[ty : ty + TILE_PX, tx : tx + TILE_PX]
        result, _ = _engine()(tile)
        for box, text, score in result or []:
            if float(score) < MIN_WORD_SCORE:
                continue
            xs = [p[0] + tx for p in box]
            ys = [p[1] + ty for p in box]
            words.append(
                OcrWord(
                    text=text.strip(),
                    bbox=(min(xs) / s, min(ys) / s, max(xs) / s, max(ys) / s),
                )
            )
    return _dedupe_words(words)


def _dedupe_words(words: list[OcrWord]) -> list[OcrWord]:
    kept: list[OcrWord] = []
    for word in words:  # duplicates come from tile overlap zones
        cx, cy = word.center
        if any(
            k.text == word.text
            and abs(k.center[0] - cx) < WORD_DEDUPE_DIST_PT
            and abs(k.center[1] - cy) < WORD_DEDUPE_DIST_PT
            for k in kept
        ):
            continue
        kept.append(word)
    return kept


def _word_area(word: OcrWord) -> float:
    return (word.bbox[2] - word.bbox[0]) * (word.bbox[3] - word.bbox[1])


def annotate_candidates(candidates: list[Candidate], words: list[OcrWord]) -> None:
    """Flag text-containing candidates and attach the nearest dimension label."""
    dims = [(word, parse_dimension(word.text)) for word in words]
    dims = [(word, d) for word, d in dims if d]

    for c in candidates:
        b = c.polygon.bounds
        box_area = (b[2] - b[0]) * (b[3] - b[1])
        if not c.contains_text:
            c.contains_text = any(
                b[0] <= word.center[0] <= b[2]
                and b[1] <= word.center[1] <= b[3]
                and _word_area(word) <= TEXT_IN_BOX_MAX_RATIO * box_area
                and c.polygon.contains(Point(word.center))
                for word in words
            )

        best_d, best_word = None, None
        cx, cy = c.polygon.centroid.x, c.polygon.centroid.y
        for word, parsed in dims:
            if ("diameter_mm" in parsed) != (c.kind == "hole"):
                continue
            wx, wy = word.center
            d = ((wx - cx) ** 2 + (wy - cy) ** 2) ** 0.5
            if d <= MAX_ASSOC_DIST_PT and (best_d is None or d < best_d):
                best_d, best_word = d, word
        if best_word is not None:
            c.dimension_text = best_word.text


def annotated_ratio(c: Candidate) -> float | None:
    """annotated_mm / measured_mm — the page's drawing scale if consistent."""
    if not c.dimension_text:
        return None
    parsed = parse_dimension(c.dimension_text)
    if not parsed:
        return None
    if c.kind == "hole" and "diameter_mm" in parsed:
        measured = c.measured_dims.get("diameter_mm")
        return parsed["diameter_mm"] / measured if measured else None
    if c.kind == "slot" and "length_mm" in parsed:
        measured = c.measured_dims.get("length_mm")
        return parsed["length_mm"] / measured if measured else None
    return None
