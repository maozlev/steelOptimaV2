"""High-DPI region renders for table crops and cells.

The stored page render is clamped to 8000px — ~170 effective DPI on an A0 sheet,
too soft to OCR stroke-drawn glyphs. Table regions are small, so they are rendered
straight from the PDF at high DPI instead.

get_pixmap's clip is in DISPLAY (rotated) coordinates — the same space as
page.rect and TableGrid.bbox — so no derotation is applied here. (Verified against
the 270°-rotated 833.1 sheets: derotating the clip yields an empty pixmap.)
"""

import fitz

from app.tables.grid import TableGrid

REGION_DPI = 600
MAX_REGION_PX = 4000
PAD_PT = 3.0


def render_region(
    page: fitz.Page,
    bbox: tuple[float, float, float, float],
    dpi: int = REGION_DPI,
    pad_pt: float = PAD_PT,
    max_px: int = MAX_REGION_PX,
) -> bytes:
    """PNG of a display-space rect, long edge clamped to max_px.

    VLM callers must pass a small max_px (~1000): a 2300px crop makes a 9B
    vision model take minutes per call instead of seconds."""
    rect = fitz.Rect(*bbox) + (-pad_pt, -pad_pt, pad_pt, pad_pt)
    rect = rect & page.rect  # never ask for pixels off the sheet
    zoom = dpi / 72.0
    long_edge = max(rect.width, rect.height, 1.0)
    zoom = min(zoom, max_px / long_edge)
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=rect)
    return pix.tobytes("png")


def render_cell(
    page: fitz.Page,
    grid: TableGrid,
    row: int,
    col: int,
    dpi: int = REGION_DPI,
    pad_pt: float = 1.0,
) -> bytes:
    return render_region(page, grid.cell_rect(row, col), dpi=dpi, pad_pt=pad_pt)


def render_rows_strip(
    page: fitz.Page,
    grid: TableGrid,
    row_start: int,
    row_end: int,
    dpi: int = REGION_DPI,
) -> bytes:
    """PNG spanning full table width from row_start to row_end inclusive."""
    x0, _, x1, _ = grid.bbox
    y0 = grid.row_edges[row_start]
    y1 = grid.row_edges[row_end + 1]
    return render_region(page, (x0, y0, x1, y1), dpi=dpi)
