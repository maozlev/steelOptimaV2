import sys
import time
from collections import Counter
from pathlib import Path

import fitz

sys.path.insert(0, ".")
from app.extraction.raster import extract_raster_candidates  # noqa: E402
from app.extraction.scoring import score_candidates  # noqa: E402
from app.ingestion.page_classifier import classify_page  # noqa: E402
from app.ingestion.renderer import render_page  # noqa: E402

TMP = Path("tmp_raster")
TMP.mkdir(exist_ok=True)


def make_raster_pdf(src: Path, out: Path, dpi: int = 150) -> None:
    with fitz.open(src) as doc:
        page = doc[0]
        pix = page.get_pixmap(dpi=dpi)
        new = fitz.open()
        p = new.new_page(width=page.rect.width, height=page.rect.height)
        p.insert_image(p.rect, pixmap=pix)
        new.save(str(out))
        new.close()


for name in ["A (3).pdf", "A (4).pdf"]:
    src = Path("../pdfs") / name
    raster_pdf = TMP / f"raster_{name.replace(' ', '_')}"
    make_raster_pdf(src, raster_pdf)
    with fitz.open(raster_pdf) as doc:
        page = doc[0]
        kind = classify_page(page)
        png = TMP / f"{raster_pdf.stem}.png"
        eff_dpi = render_page(page, png, 300)
        t0 = time.time()
        cands = extract_raster_candidates(png, eff_dpi, abs(page.rect))
        dt = time.time() - t0
        scores = score_candidates(cands)
        conf = Counter(c.kind for c, s in zip(cands, scores) if s >= 0.65)
        esc = Counter(c.kind for c, s in zip(cands, scores) if s < 0.65)
        print(
            f"{name}: kind={kind} dpi={eff_dpi} cands={len(cands)} "
            f"cv_time={dt:.1f}s | confident: {dict(conf)} | escalate: {dict(esc)}"
        )
