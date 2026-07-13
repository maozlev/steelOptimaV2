"""Cell reading: recognition-only OCR on ink-cropped cells, VLM strips on demand.

Two findings drive everything here, both verified on the NCD5168 steel BOM
(re-check with tools/eval_tables.py before changing):

- CAD stroke glyphs are hairlines. Rendered with anti-aliasing they are a faint
  gray wisp (min level ~172) that RapidOCR reads at 0/30. Rendered with AA OFF,
  binarized and thickened, the digits are crisp.
- RapidOCR's DETECTION stage is the failure mode, not recognition: sparse
  letter-spaced digits on a big white cell detect as fragments ("9000" -> "9").
  Cropping each cell to its ink bounding box and running RECOGNITION ONLY reads
  120/120 numeric cells and is ~25x faster (no det pass).

Digits are language-neutral, so numeric columns ride on OCR; Hebrew descriptions
(which this OCR model cannot read) and cells the OCR is unsure about go to the
VLM, whose reads are compared against the OCR, never blindly trusted.
"""

from dataclasses import dataclass

import cv2
import fitz
import numpy as np

from app.extraction.ocr import _engine
from app.tables.grid import TableGrid
from app.tables.regions import render_region
from app.vlm.client import OllamaVlmClient
from app.vlm.prompts import (
    ROW_VALUES_SCHEMA,
    transcribe_column_prompt,
    transcribe_row_prompt,
)

CELL_INSET_PT = 1.6  # drop rule lines + snap tolerance at the cell borders
MAX_OCR_PX = 12000  # OCR render cap; ~70MB gray for the largest sample table
INK_LEVEL = 128  # the render is binarized: ink is plain black
MIN_INK_PIXELS = 12  # fewer dark pixels than this means the cell is empty
INK_PAD_PX = 12  # margin kept around the ink when cropping for recognition
LINE_GAP_PX = 6  # blank rows separating text lines in a multi-line cell
EMPTY_CONF = 0.99
VLM_STRIP_MAX_ROWS = 10  # column-strip transcriptions per call
VLM_MAX_PX = 1024  # bigger crops put a 9B vision model into minutes-per-call


@dataclass
class CellRead:
    raw_ocr: str | None = None
    ocr_conf: float = 0.0
    vlm_value: str | None = None
    value: str | None = None
    source: str = "empty"  # ocr | vlm | fused | empty | manual

    def as_dict(self, col: int) -> dict:
        return {
            "col": col,
            "raw_ocr": self.raw_ocr,
            "ocr_conf": round(self.ocr_conf, 3),
            "vlm_value": self.vlm_value,
            "value": self.value,
            "source": self.source,
        }


class TableImage:
    """The table's single binarized high-DPI render plus the pt->px mapping."""

    def __init__(self, page: fitz.Page, grid: TableGrid, dpi: int):
        self.grid = grid
        rect = fitz.Rect(*grid.bbox) & page.rect
        zoom = min(dpi / 72.0, MAX_OCR_PX / max(rect.width, rect.height, 1.0))
        aa_before = fitz.TOOLS.show_aa_level().get("graphics", 8)
        fitz.TOOLS.set_aa_level(0)
        try:
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=rect)
        finally:
            fitz.TOOLS.set_aa_level(aa_before)
        gray = cv2.imdecode(
            np.frombuffer(pix.tobytes("png"), dtype=np.uint8), cv2.IMREAD_GRAYSCALE
        )
        _, bw = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY)
        # erode white = thicken the 1px strokes into something OCR-legible
        self.bw = cv2.erode(bw, np.ones((3, 3), np.uint8))
        h, w = self.bw.shape
        self.sx = w / rect.width
        self.sy = h / rect.height
        self.origin = (rect.x0, rect.y0)

    def region_image(self, bbox: tuple[float, float, float, float]) -> np.ndarray:
        x0, y0, x1, y1 = bbox
        ox, oy = self.origin
        px0 = max(int((x0 - ox) * self.sx), 0)
        py0 = max(int((y0 - oy) * self.sy), 0)
        px1 = int((x1 - ox) * self.sx)
        py1 = int((y1 - oy) * self.sy)
        if px1 <= px0 or py1 <= py0:
            return self.bw[0:0, 0:0]
        return self.bw[py0:py1, px0:px1]

    def cell_image(self, row: int, col: int) -> np.ndarray:
        x0, y0, x1, y1 = self.grid.cell_rect(row, col)
        return self.region_image(
            (
                x0 + CELL_INSET_PT,
                y0 + CELL_INSET_PT,
                x1 - CELL_INSET_PT,
                y1 - CELL_INSET_PT,
            )
        )


def _ink_crop(img: np.ndarray) -> np.ndarray | None:
    if img.size == 0:
        return None
    ys, xs = np.where(img < INK_LEVEL)
    if len(xs) < MIN_INK_PIXELS:
        return None
    return img[
        max(int(ys.min()) - INK_PAD_PX, 0) : int(ys.max()) + INK_PAD_PX,
        max(int(xs.min()) - INK_PAD_PX, 0) : int(xs.max()) + INK_PAD_PX,
    ]


def _split_lines(img: np.ndarray) -> list[np.ndarray]:
    """Split a multi-line cell on blank horizontal gaps — recognition-only OCR
    reads exactly one line."""
    ink_rows = (img < INK_LEVEL).sum(axis=1) > 0
    lines: list[tuple[int, int]] = []
    start, gap = None, 0
    for i, has_ink in enumerate(ink_rows):
        if has_ink:
            if start is None:
                start = i
            gap = 0
        elif start is not None:
            gap += 1
            if gap >= LINE_GAP_PX:
                lines.append((start, i - gap + 1))
                start = None
    if start is not None:
        lines.append((start, len(ink_rows)))
    if len(lines) <= 1:
        return [img]
    pad = LINE_GAP_PX
    return [img[max(a - pad, 0) : b + pad] for a, b in lines]


def _rec_only(img: np.ndarray) -> tuple[str, float] | None:
    if img.shape[0] < 8 or img.shape[1] < 8:
        return None
    try:
        result, _ = _engine()(img, use_det=False, use_cls=False)
    except Exception:
        # RapidOCR raises (e.g. ResizeImgError) on degenerate crops — a failed
        # read, not a failed job
        return None
    if not result:
        return None
    text = str(result[0][0]).strip()
    conf = float(result[0][1])
    return (text, conf) if text else None


def ocr_cell(img: np.ndarray) -> CellRead:
    crop = _ink_crop(img)
    if crop is None:
        return CellRead(source="empty", ocr_conf=EMPTY_CONF, value="")
    texts: list[str] = []
    confs: list[float] = []
    for line in _split_lines(crop):
        line_crop = _ink_crop(line)
        if line_crop is None:
            continue
        read = _rec_only(line_crop)
        if read:
            texts.append(read[0])
            confs.append(read[1])
    if not texts:
        # ink is there but the OCR saw nothing — a zero-confidence read, not an
        # empty cell; it must end up in front of the VLM or a human
        return CellRead(raw_ocr=None, ocr_conf=0.0, value=None, source="ocr")
    text = " ".join(texts)
    return CellRead(raw_ocr=text, ocr_conf=min(confs), value=text, source="ocr")


def read_matrix(
    page: fitz.Page, grid: TableGrid, data_rows: list[int], dpi: int
) -> list[list[CellRead]]:
    """OCR every cell of the given grid rows."""
    image = TableImage(page, grid, dpi)
    return [
        [ocr_cell(image.cell_image(r, c)) for c in range(grid.n_cols)]
        for r in data_rows
    ]


def read_strip_text(page: fitz.Page, bbox, dpi: int) -> list[str]:
    """Line texts from an arbitrary region (e.g. the 'Total Weight' footer above
    or below a table). Same preprocessing as cells, one rec pass per line."""

    rect = fitz.Rect(*bbox) & page.rect
    if rect.is_empty:
        return []
    fake_grid = TableGrid(
        bbox=(rect.x0, rect.y0, rect.x1, rect.y1),
        col_edges=[rect.x0, rect.x1],
        row_edges=[rect.y0, rect.y1],
    )
    image = TableImage(page, fake_grid, dpi)
    crop = _ink_crop(image.bw)
    if crop is None:
        return []
    out = []
    for line in _split_lines(crop):
        line_crop = _ink_crop(line)
        read = _rec_only(line_crop) if line_crop is not None else None
        if read:
            out.append(read[0])
    return out


def _values_of(data) -> list | None:
    """Ollama does not reliably enforce the format schema (see prompts.py) — the
    model sometimes replies with a bare list instead of {"values": [...]}."""
    if isinstance(data, dict) and isinstance(data.get("values"), list):
        return data["values"]
    if isinstance(data, list):
        return data
    return None


def _require_values(data) -> None:
    if _values_of(data) is None:
        raise ValueError("no values array in reply")


def vlm_read_column(
    page: fitz.Page,
    grid: TableGrid,
    data_rows: list[int],
    col: int,
    client: OllamaVlmClient,
    dpi: int,
) -> tuple[dict[int, str], int]:
    """Transcribe one column in strips. Returns ({grid_row: text}, calls_made)."""
    out: dict[int, str] = {}
    calls = 0
    for start in range(0, len(data_rows), VLM_STRIP_MAX_ROWS):
        chunk = data_rows[start : start + VLM_STRIP_MAX_ROWS]
        bbox = (
            grid.col_edges[col],
            grid.row_edges[chunk[0]],
            grid.col_edges[col + 1],
            grid.row_edges[chunk[-1] + 1],
        )
        png = render_region(page, bbox, dpi=dpi, pad_pt=1.0, max_px=VLM_MAX_PX)
        result = client.chat_json(
            png,
            transcribe_column_prompt(len(chunk)),
            ROW_VALUES_SCHEMA,
            validate=_require_values,
        )
        calls += 1
        if result.ok:
            for row, value in zip(chunk, _values_of(result.data) or []):
                out[row] = str(value).strip()
    return out, calls


def vlm_read_row(
    page: fitz.Page,
    grid: TableGrid,
    row: int,
    client: OllamaVlmClient,
    dpi: int,
) -> list[str] | None:
    bbox = (
        grid.bbox[0],
        grid.row_edges[row],
        grid.bbox[2],
        grid.row_edges[row + 1],
    )
    png = render_region(page, bbox, dpi=dpi, pad_pt=1.0, max_px=VLM_MAX_PX)
    result = client.chat_json(
        png,
        transcribe_row_prompt(grid.n_cols),
        ROW_VALUES_SCHEMA,
        validate=_require_values,
    )
    if not result.ok:
        return None
    values = [str(v).strip() for v in (_values_of(result.data) or [])]
    if len(values) != grid.n_cols:
        return None
    return values
