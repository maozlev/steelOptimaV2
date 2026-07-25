"""Score the extractor against tests/fixtures/ground_truth.json.

Run:  uv run python tools/eval_detection.py

Reports, per drawing:
  recall     - of the cutouts that really exist, how many did we find?
  precision  - of the cutouts we reported, how many are real?
  dim error  - how far off are the sizes, in real-world mm?

Recall is the number that matters most: Maoz's rule is "never miss a real hole" - a
missed cutout means a part is manufactured wrong, a false positive only costs a click.

This module is also a LIBRARY. `reported_cutouts()` and `score_drawing()` are imported
by tests/test_detection_accuracy.py (the regression gate) and by tools/export_fixture.py
(which uses them to refuse exporting a document whose review is out of date). One
definition of "what the pipeline found" - do not write a second one.
"""

import json
import sys
import tempfile
from pathlib import Path

import fitz

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.bom.shapes import shape_metrics  # noqa: E402
from app.config import settings  # noqa: E402
from app.extraction.scoring import score_candidates  # noqa: E402
from app.extraction.service import _page_candidates  # noqa: E402
from app.ingestion.page_classifier import classify_page  # noqa: E402
from app.ingestion.renderer import render_page  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
PDFS = ROOT / "pdfs"
TRUTH = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "ground_truth.json"

# a reported cutout matches a true one if the shape agrees and every dimension is
# within this fraction - tight enough that a scale error can never pass
DIM_TOLERANCE = 0.05

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg"}


def load_truth(path: Path = TRUTH) -> dict:
    truth = json.loads(path.read_text(encoding="utf-8"))
    return {k: v for k, v in truth.items() if not k.startswith("_")}


def open_document(pdf: Path) -> fitz.Document:
    """Open a fixture the way ingestion does.

    Images are converted to PDF first; only the converted page classifies as "raster"
    (a directly-opened image reports no embedded images and would silently take the
    vector path, testing something the product never runs).
    """
    if pdf.suffix.lower() in IMAGE_SUFFIXES:
        with fitz.open(pdf) as img:
            return fitz.open("pdf", img.convert_to_pdf())
    return fitz.open(pdf)


def reported_cutouts(pdf: Path, tag: str = "") -> list[dict]:
    """What the CURRENT pipeline would put in the BOM for this file.

    Auto-approved only - candidates scoring below the finalize threshold are the
    operator's problem, not the BOM's. Each entry is `shape_metrics` plus the
    centroid in page points, so callers can check WHERE a cutout is and not only
    that something of the right size exists somewhere.
    """
    doc = open_document(pdf)
    out: list[dict] = []
    try:
        for pno in range(doc.page_count):
            page = doc[pno]
            page_kind = classify_page(page)
            rp, dpi = "", settings.render_dpi
            if page_kind == "raster":
                # scans go through the CV pipeline exactly as ingestion runs it
                rp = Path(tempfile.gettempdir()) / f"eval_{tag or pdf.stem}_{pno}.png"
                dpi = render_page(page, rp, settings.render_dpi)

            class _Row:  # _page_candidates only reads these
                index = pno
                kind = page_kind
                render_path = str(rp)
                render_dpi = dpi

            cands = _page_candidates(doc, _Row())
            for c, s in zip(cands, score_candidates(cands)):
                if s >= settings.finalize_threshold:
                    m = shape_metrics(c.polygon, c.kind)
                    m["center_pt"] = [
                        round(c.polygon.centroid.x, 2),
                        round(c.polygon.centroid.y, 2),
                    ]
                    m["page"] = pno
                    out.append(m)
    finally:
        doc.close()
    return out


def truth_dims(c: dict) -> list[float] | None:
    if c.get("diameter_mm") is not None:
        return [c["diameter_mm"]]
    if c.get("length_mm") is not None and c.get("width_mm") is not None:
        return [c["length_mm"], c["width_mm"]]
    return None


def found_dims(m: dict) -> list[float]:
    d = m["dims"]
    if "diameter_mm" in d:
        return [d["diameter_mm"]]
    if "length_mm" in d:
        return [d["length_mm"], d["width_mm"]]
    return [d["bbox_w_mm"], d["bbox_h_mm"]]


def close(a: list[float], b: list[float], tol: float = DIM_TOLERANCE) -> bool:
    return len(a) == len(b) and all(
        abs(x - y) <= tol * max(y, 1e-9) for x, y in zip(a, b)
    )


def score_drawing(name: str, spec: dict, pdf: Path) -> dict:
    """Recall / precision for one drawing against its ground-truth entry.

    Returns `scale_known=False` when the fixture has no scale: sizes cannot be
    scored in real-world mm, so the drawing is excluded rather than guessed at.
    """
    reported = reported_cutouts(pdf, tag=name)
    n_true = sum(c["qty"] for c in spec["cutouts"])
    scale = spec.get("scale")
    if scale is None:
        return {
            "name": name,
            "scale_known": False,
            "true": n_true,
            "found": len(reported),
            "reported": reported,
        }

    # raster measures the ink INTERIOR of a hole, which under-reads small ones by a
    # stroke width - scans may loosen the gate, stating it in the fixture
    tolerance = spec.get("dim_tolerance", DIM_TOLERANCE)

    # match reported -> true, greedily, consuming each true slot once
    remaining = []
    for c in spec["cutouts"]:
        remaining += [c] * c["qty"]

    hits = 0
    unmatched: list[dict] = []
    for m in reported:
        got = [d * scale for d in found_dims(m)]  # paper mm -> real mm
        for i, t in enumerate(remaining):
            want = truth_dims(t)
            if want is None:
                continue
            if m["shape"] == t["shape"] and close(sorted(got), sorted(want), tolerance):
                hits += 1
                remaining.pop(i)
                break
        else:
            unmatched.append(m)

    return {
        "name": name,
        "scale_known": True,
        "true": n_true,
        "found": len(reported),
        "hits": hits,
        "recall": hits / n_true if n_true else 0.0,
        "precision": hits / len(reported) if reported else 0.0,
        "missed": remaining,
        "spurious": unmatched,
        "reported": reported,
    }


def forbidden_hits(spec: dict, reported: list[dict], page_sizes: dict) -> list[dict]:
    """Reported cutouts whose centre lands in a region the fixture forbids.

    `must_not_detect` in the fixtures is prose that nothing reads. `forbidden_regions`
    is the same statement in fractional page coordinates, so it can actually fail a
    test: the title block, the boxed dimension callouts, the feature-control frame.
    """
    hits = []
    for region in spec.get("forbidden_regions", []):
        pno = region.get("page", 0)
        w, h = page_sizes.get(pno, (0, 0))
        if not w or not h:
            continue
        x0, y0, x1, y1 = region["rect"]
        for m in reported:
            if m.get("page", 0) != pno:
                continue
            cx, cy = m["center_pt"]
            if x0 * w <= cx <= x1 * w and y0 * h <= cy <= y1 * h:
                hits.append({"region": region, "cutout": m})
    return hits


def main() -> int:
    truth = load_truth()
    total_true = total_found = total_hit = 0
    unresolved = []
    per_drawing: list[tuple[float, float]] = []

    print(f"{'drawing':<38} {'true':>5} {'found':>6} {'hit':>4} {'recall':>7} {'prec':>6}  notes")
    print("-" * 104)

    for name, spec in truth.items():
        pdf = PDFS / name
        if not pdf.exists():
            continue

        r = score_drawing(name, spec, pdf)
        if not r["scale_known"]:
            unresolved.append(name)
            print(
                f"{name[:37]:<38} {r['true']:>5} {r['found']:>6} {'-':>4} {'-':>7} {'-':>6}"
                f"  SCALE UNKNOWN - cannot score sizes"
            )
            continue

        total_true += r["true"]
        total_found += r["found"]
        total_hit += r["hits"]
        per_drawing.append((r["recall"], r["precision"]))

        miss = sorted({t["shape"] for t in r["missed"]})
        note = "" if not miss else f"MISSED: {', '.join(miss)}"
        print(
            f"{name[:37]:<38} {r['true']:>5} {r['found']:>6} {r['hits']:>4} "
            f"{r['recall']:>6.0%} {r['precision']:>5.0%}  {note}"
        )

    print("-" * 104)
    r = total_hit / total_true if total_true else 0
    p = total_hit / total_found if total_found else 0
    print(
        f"{'per-cutout (micro)':<38} {total_true:>5} {total_found:>6} "
        f"{total_hit:>4} {r:>6.0%} {p:>5.0%}   <- flattered by A (4): 293 identical holes"
    )
    if per_drawing:
        mr = sum(x for x, _ in per_drawing) / len(per_drawing)
        mp = sum(y for _, y in per_drawing) / len(per_drawing)
        print(
            f"{'per-DRAWING (macro)':<38} {'':>5} {'':>6} {'':>4} "
            f"{mr:>6.0%} {mp:>5.0%}   <- the number that actually matters"
        )
    if unresolved:
        print(f"\nscale unresolved, excluded from the score: {', '.join(unresolved)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
