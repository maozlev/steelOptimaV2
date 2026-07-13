"""Score the extractor against tests/fixtures/ground_truth.json.

Run:  uv run python tools/eval_detection.py

Reports, per drawing:
  recall     - of the cutouts that really exist, how many did we find?
  precision  - of the cutouts we reported, how many are real?
  dim error  - how far off are the sizes, in real-world mm?

Recall is the number that matters most: Maoz's rule is "never miss a real hole" - a
missed cutout means a part is manufactured wrong, a false positive only costs a click.
"""

import json
import sys
from pathlib import Path

import fitz

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.bom.shapes import shape_metrics  # noqa: E402
from app.config import settings  # noqa: E402
from app.extraction.scoring import score_candidates  # noqa: E402
from app.extraction.service import _page_candidates  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
PDFS = ROOT / "pdfs"
TRUTH = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "ground_truth.json"

# a reported cutout matches a true one if the shape agrees and every dimension is
# within this fraction - tight enough that a scale error can never pass
DIM_TOLERANCE = 0.05


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


def close(a: list[float], b: list[float]) -> bool:
    return len(a) == len(b) and all(
        abs(x - y) <= DIM_TOLERANCE * max(y, 1e-9) for x, y in zip(a, b)
    )


def main() -> int:
    truth = json.loads(TRUTH.read_text(encoding="utf-8"))
    total_true = total_found = total_hit = 0
    unresolved = []
    per_drawing: list[tuple[float, float]] = []

    print(f"{'drawing':<38} {'true':>5} {'found':>6} {'hit':>4} {'recall':>7} {'prec':>6}  notes")
    print("-" * 104)

    for name, spec in truth.items():
        if name.startswith("_"):
            continue
        pdf = PDFS / name
        if not pdf.exists():
            continue

        scale = spec.get("scale")
        doc = fitz.open(pdf)

        # what the pipeline would actually put in the BOM: auto-approved only
        reported: list[dict] = []
        for pno in range(doc.page_count):
            page = doc[pno]

            class _Row:  # _page_candidates only reads these
                index = pno
                kind = "vector"
                render_path = ""
                render_dpi = settings.render_dpi

            cands = _page_candidates(doc, _Row())
            for c, s in zip(cands, score_candidates(cands)):
                if s >= settings.finalize_threshold:
                    reported.append(shape_metrics(c.polygon, c.kind))

        n_true = sum(c["qty"] for c in spec["cutouts"])
        n_found = len(reported)

        if scale is None:
            unresolved.append(name)
            print(
                f"{name[:37]:<38} {n_true:>5} {n_found:>6} {'—':>4} {'—':>7} {'—':>6}"
                f"  SCALE UNKNOWN — cannot score sizes"
            )
            continue

        # match reported -> true, greedily, consuming each true slot once
        remaining = []
        for c in spec["cutouts"]:
            remaining += [c] * c["qty"]

        hits = 0
        for m in reported:
            got = [d * scale for d in found_dims(m)]  # paper mm -> real mm
            for i, t in enumerate(remaining):
                want = truth_dims(t)
                if want is None:
                    continue
                if m["shape"] == t["shape"] and close(sorted(got), sorted(want)):
                    hits += 1
                    remaining.pop(i)
                    break

        recall = hits / n_true if n_true else 0.0
        prec = hits / n_found if n_found else 0.0
        total_true += n_true
        total_found += n_found
        total_hit += hits
        per_drawing.append((recall, prec))

        miss = [f"{t['shape']}" for t in remaining]
        note = "" if not miss else f"MISSED: {', '.join(sorted(set(miss)))}"
        print(
            f"{name[:37]:<38} {n_true:>5} {n_found:>6} {hits:>4} "
            f"{recall:>6.0%} {prec:>5.0%}  {note}"
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
