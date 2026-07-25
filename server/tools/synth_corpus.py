"""Build and score the synthetic PDF corpus.

    uv run python tools/synth_corpus.py                       # build + score everything
    uv run python tools/synth_corpus.py --family notch slot   # one or more families
    uv run python tools/synth_corpus.py --keep ../synth_out   # also write the PDFs
    uv run python tools/synth_corpus.py --failures            # list every failing case

WHAT THE COLUMNS MEAN
  detect   of the real cutouts drawn, how many produced a candidate AT ALL.
           This is "never miss a real hole" and it is the number that matters.
  approve  how many reached the finalize threshold and would enter the BOM unattended.
           Only ASSERTED for shapes with an ideal fit; reported for the rest.
  shape    of those approved, how many were classified as the shape actually drawn.
  spurious approved candidates matching nothing that was drawn, plus anything reported
           inside a region declared empty (a title block, a detail bubble).

A corpus that reports all-green is a corpus that was built too easy. Read the failure
surface, not the totals.
"""

from __future__ import annotations

import argparse
import math
import sys
from collections import defaultdict
from pathlib import Path

import fitz

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.extraction.vector import PT_TO_MM  # noqa: E402
from tools.eval_detection import candidates_in_doc  # noqa: E402
from tools.synth.cases import Case, PAGE_H, PAGE_W, all_cases  # noqa: E402

# a candidate is "the same feature" as an expectation if its centre is within this of
# the drawn centre - generous enough for stroke width, tight enough that the neighbouring
# hole in a 34pt-pitch grid can never be mistaken for it
def match_tol(bbox: tuple[float, float]) -> float:
    return max(3.0, 0.25 * min(bbox))


SIZE_TOL = 0.35  # sizes may differ by this fraction (stroke width dominates small holes)


def _size_ok(want: tuple[float, float], got: dict) -> bool:
    """Compare the size the pipeline REPORTS against the size that was drawn.

    Not the axis-aligned bounding box: a rectangle rotated 45 degrees has an AABB far
    larger than its length x width, and comparing against that would fail every rotated
    case for a reason that has nothing to do with the pipeline. `shape_metrics` reports
    the minimum ROTATED rectangle's sides, which is the measurement a fabricator cuts to,
    so that is what gets checked.
    """
    d = got["dims"]
    if "diameter_mm" in d:
        have = [d["diameter_mm"] / PT_TO_MM] * 2
    elif "length_mm" in d:
        have = [d["length_mm"] / PT_TO_MM, d["width_mm"] / PT_TO_MM]
    else:
        have = [d["bbox_w_mm"] / PT_TO_MM, d["bbox_h_mm"] / PT_TO_MM]
    return all(
        abs(a - b) <= SIZE_TOL * max(b, 1e-9)
        for a, b in zip(sorted(have), sorted(want))
    )


def build_doc(case: Case) -> fitz.Document:
    w, h = PAGE_W, PAGE_H
    if case.family == "sheet_size":
        from tools.synth.cases import PAGE_SIZE_BY_CASE

        w, h = PAGE_SIZE_BY_CASE[case.params["sheet"]]
    doc = fitz.open()
    page = doc.new_page(width=w, height=h)
    case.draw(page)
    return fitz.open("pdf", doc.tobytes())


def score_case(case: Case) -> dict:
    doc = build_doc(case)
    try:
        seen = candidates_in_doc(doc, case.id, min_score=0.0)
    finally:
        doc.close()

    approved = [c for c in seen if c["score"] >= settings.finalize_threshold]
    used: set[int] = set()
    rows = []
    for e in case.expect:
        tol = match_tol(e.bbox)
        best = None
        for i, c in enumerate(seen):
            if i in used:
                continue
            dx = c["center_pt"][0] - e.center[0]
            dy = c["center_pt"][1] - e.center[1]
            if math.hypot(dx, dy) > tol:
                continue
            if best is None or c["score"] > seen[best]["score"]:
                best = i
        if best is not None:
            used.add(best)
        c = seen[best] if best is not None else None
        got_size = bool(c) and _size_ok(e.bbox, c)
        rows.append(
            {
                "expect": e,
                "detected": c is not None,
                "approved": bool(c and c["score"] >= settings.finalize_threshold),
                "shape_ok": bool(c and (e.shape is None or c["shape"] == e.shape)),
                "size_ok": bool(got_size),
                "got": c,
            }
        )

    matched_centres = [seen[i]["center_pt"] for i in used]
    spurious = []
    for c in approved:
        if c["center_pt"] in matched_centres:
            continue
        spurious.append(c)
    forbidden = [
        c
        for c in approved
        for (x0, y0, x1, y1) in case.forbid
        if x0 <= c["center_pt"][0] <= x1 and y0 <= c["center_pt"][1] <= y1
    ]
    return {"case": case, "rows": rows, "spurious": spurious, "forbidden": forbidden}


def failures(result: dict) -> list[str]:
    """Only things the corpus ASSERTS. Approval of a non-ideal shape is reported, not
    demanded — a freeform cutout is meant to be surfaced for review, not auto-approved."""
    out = []
    for r in result["rows"]:
        e = r["expect"]
        if e.must_detect and not r["detected"]:
            out.append(f"MISSED {e.shape or 'shape'} at {e.center} size {e.bbox}")
            continue
        if e.must_approve and not r["approved"]:
            got = r["got"]
            out.append(
                f"NOT APPROVED {e.shape} at {e.center} "
                f"(score {got['score'] if got else '-'}, kind {got['kind'] if got else '-'})"
            )
        if e.must_approve and r["approved"] and not r["shape_ok"]:
            out.append(f"WRONG SHAPE at {e.center}: want {e.shape}, got {r['got']['shape']}")
        if e.must_approve and r["approved"] and not r["size_ok"]:
            out.append(
                f"WRONG SIZE at {e.center}: want {e.bbox}, got {r['got']['bbox_pt']}"
            )
    for c in result["forbidden"]:
        out.append(f"REPORTED IN A FORBIDDEN REGION: {c['shape']} at {c['center_pt']}")
    for c in result["spurious"]:
        out.append(f"SPURIOUS {c['shape']} at {c['center_pt']} score {c['score']}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--family", nargs="+", help="only these families")
    ap.add_argument("--keep", type=Path, help="write the PDFs here")
    ap.add_argument("--failures", action="store_true", help="list every failing case")
    ap.add_argument("--limit", type=int, help="stop after N cases (smoke test)")
    args = ap.parse_args()

    cases = all_cases()
    if args.family:
        cases = [c for c in cases if c.family in set(args.family)]
    if args.limit:
        cases = cases[: args.limit]
    print(f"{len(cases)} cases\n")

    if args.keep:
        args.keep.mkdir(parents=True, exist_ok=True)

    by_family: dict[str, dict] = defaultdict(
        lambda: {
            "cases": 0, "bad": 0, "expected": 0, "detected": 0,
            "approved": 0, "shape_ok": 0, "size_ok": 0,
            "spurious": 0, "forbidden": 0, "asserted_approve": 0,
        }
    )
    bad_cases: list[tuple[Case, list[str]]] = []

    for case in cases:
        if args.keep:
            doc = build_doc(case)
            doc.save(args.keep / f"{case.id}.pdf")
            doc.close()
        r = score_case(case)
        f = by_family[case.family]
        f["cases"] += 1
        for row in r["rows"]:
            f["expected"] += 1
            f["detected"] += row["detected"]
            f["approved"] += row["approved"]
            f["shape_ok"] += row["shape_ok"]
            f["size_ok"] += row["size_ok"]
            if row["expect"].must_approve:
                f["asserted_approve"] += 1
        f["spurious"] += len(r["spurious"])
        f["forbidden"] += len(r["forbidden"])
        errs = failures(r)
        if errs:
            f["bad"] += 1
            bad_cases.append((case, errs))

    hdr = (
        f"{'family':<13}{'cases':>6}{'fail':>6}{'cutouts':>9}"
        f"{'detect':>8}{'approve':>9}{'shape':>7}{'size':>7}{'spur':>6}{'forbid':>7}"
    )
    print(hdr)
    print("-" * len(hdr))
    tot = defaultdict(int)
    for name in sorted(by_family):
        f = by_family[name]
        n = max(f["expected"], 1)
        print(
            f"{name:<13}{f['cases']:>6}{f['bad']:>6}{f['expected']:>9}"
            f"{f['detected'] / n:>7.0%}{f['approved'] / n:>9.0%}"
            f"{f['shape_ok'] / n:>7.0%}{f['size_ok'] / n:>7.0%}"
            f"{f['spurious']:>6}{f['forbidden']:>7}"
        )
        for k, v in f.items():
            tot[k] += v
    print("-" * len(hdr))
    n = max(tot["expected"], 1)
    print(
        f"{'TOTAL':<13}{tot['cases']:>6}{tot['bad']:>6}{tot['expected']:>9}"
        f"{tot['detected'] / n:>7.0%}{tot['approved'] / n:>9.0%}"
        f"{tot['shape_ok'] / n:>7.0%}{tot['size_ok'] / n:>7.0%}"
        f"{tot['spurious']:>6}{tot['forbidden']:>7}"
    )
    print(f"\n{tot['bad']}/{tot['cases']} cases fail an assertion.")

    if args.failures:
        print("\n--- failing cases ---")
        for case, errs in bad_cases:
            print(f"\n{case.id}  [{case.family}] {case.note}")
            for e in errs[:4]:
                print(f"    {e}")
            if len(errs) > 4:
                print(f"    ... +{len(errs) - 4} more")
    else:
        by_reason: dict[str, int] = defaultdict(int)
        for _, errs in bad_cases:
            for e in errs:
                by_reason[e.split(" at ")[0].split(":")[0]] += 1
        if by_reason:
            print("\nfailures by reason:")
            for k, v in sorted(by_reason.items(), key=lambda kv: -kv[1]):
                print(f"  {v:>5}  {k}")
            print("\n--failures lists them individually.")
    if args.keep:
        print(f"\nPDFs in {args.keep}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
