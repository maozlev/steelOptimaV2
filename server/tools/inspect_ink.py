"""How will the pipeline read this drawing, and why?

    uv run python tools/inspect_ink.py                    # every sample drawing
    uv run python tools/inspect_ink.py ../pdfs/foo.pdf    # one file

Run this FIRST when a new drawing behaves oddly. Everything downstream — which strokes get
polygonized, which become candidates, where the scale is measured from — depends on the
convention decision made in extraction/ink.py, and a wrong decision there looks like a
detection bug everywhere else.

It prints the decision, the evidence behind it, and what came out.
"""

import sys
from collections import Counter
from pathlib import Path

import fitz

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.extraction.ink import (  # noqa: E402
    ANNOTATION,
    FRAME,
    GEOMETRY,
    MIN_ANNOTATION_SHARE,
    THIN_TO_THICK_RATIO,
    classify_path,
    split_ink,
)
from app.extraction.scale import resolve_scale  # noqa: E402
from app.extraction.scoring import score_candidates  # noqa: E402
from app.extraction.vector import extract_candidates  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


def convention_of(page: fitz.Page) -> tuple[str, str]:
    """(convention, the evidence for it) — mirrors split_ink's decision."""
    paths = page.get_drawings()
    strokes = [p for p in paths if p.get("color") is not None]
    if not strokes:
        return "none", "no stroked paths at all"

    coloured = [p for p in strokes if classify_path(p) == ANNOTATION]
    share = len(coloured) / len(strokes)
    if share >= MIN_ANNOTATION_SHARE:
        return (
            "colour",
            f"{len(coloured)}/{len(strokes)} strokes ({share:.0%}) are annotation-coloured",
        )

    widths = sorted({round(p["width"], 2) for p in strokes if p.get("width")})
    if len(widths) < 2:
        return "fail-safe", f"one colour AND one width ({widths}) — nothing to separate by"
    return (
        "stroke width",
        f"only {len(coloured)}/{len(strokes)} strokes are coloured, "
        f"but widths are {widths} — thin/thick cut at {widths[0] * THIN_TO_THICK_RATIO:.2f}",
    )


def report(pdf: Path) -> None:
    page = fitz.open(pdf)[0]
    paths = page.get_drawings()
    conv, why = convention_of(page)

    geometry, annotation = split_ink(page)
    cands = extract_candidates(page)
    scores = score_candidates(cands)
    accepted = sum(1 for s in scores if s >= settings.finalize_threshold)
    sc = resolve_scale(page, cands)

    print(f"\n{'=' * 78}\n{pdf.name}")
    print(f"  sheet        {page.rect.width * 25.4 / 72:.0f} x "
          f"{page.rect.height * 25.4 / 72:.0f} mm of paper")
    print(f"  CONVENTION   {conv.upper()}")
    print(f"    because    {why}")

    by_colour = Counter(classify_path(p) for p in paths)
    print(f"  by colour    geometry={by_colour[GEOMETRY]} "
          f"annotation={by_colour[ANNOTATION]} frame={by_colour[FRAME]}")
    print(f"  SPLIT        {len(geometry)} geometry paths / {len(annotation)} annotation "
          f"(of {len(paths)})")

    if not geometry:
        print("  !! NOTHING left as geometry — this page would extract nothing")

    print(f"  candidates   {len(cands)} found, {accepted} above the "
          f"{settings.finalize_threshold} finalize threshold")
    kinds = Counter(c.kind for c, s in zip(cands, scores) if s >= settings.finalize_threshold)
    if kinds:
        print(f"               {dict(kinds)}")
    scale = "could not be established" if sc.scale is None else (
        f"{sc.scale:.3f} ({'confident' if sc.confident else 'UNCONFIRMED — operator must set it'})"
    )
    print(f"  scale        {scale}")
    if sc.note:
        print(f"               {sc.note}")


def main() -> int:
    targets = (
        [Path(a) for a in sys.argv[1:]]
        if len(sys.argv) > 1
        else sorted((ROOT / "pdfs").glob("*.pdf"))
    )
    for pdf in targets:
        if pdf.exists():
            report(pdf)
        else:
            print(f"missing: {pdf}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
