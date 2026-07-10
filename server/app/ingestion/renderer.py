from pathlib import Path

import fitz

MAX_RENDER_PX = 8000


def render_page(page: fitz.Page, out_path: Path, dpi: int) -> int:
    """Render to PNG, clamping the long edge to MAX_RENDER_PX.

    Returns the effective DPI actually used (large-format sheets like A0/A1
    exceed MuPDF's pixmap limit at the requested DPI).
    """
    long_edge_pt = max(page.rect.width, page.rect.height)
    max_dpi = int(MAX_RENDER_PX * 72 / long_edge_pt)
    effective_dpi = min(dpi, max_dpi)

    pix = page.get_pixmap(dpi=effective_dpi)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pix.save(str(out_path))
    return effective_dpi
