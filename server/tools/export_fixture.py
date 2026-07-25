"""Turn a document the operator has already reviewed into an eval fixture.

WHY THIS EXISTS
The limiting factor on detection accuracy is labelled drawings, not algorithms — and
the app has been making labelled drawings all along and throwing them away. Finalize
(`app/api/documents.py`) records every cutout as approved or rejected against a scale
the operator confirmed. That is exactly what `tests/fixtures/ground_truth.json` holds.
This copies it out, so every drawing Maoz reviews becomes a permanent regression
fixture at no extra cost.

WHAT IT EXPORTS
  approved cutouts -> "cutouts": the things that must be found, with qty and centre
  rejected cutouts -> "forbidden_regions": human-confirmed FALSE POSITIVES, with the
                      exact rectangle they sat in. Real drawings have never had this;
                      `must_not_detect` is prose that nothing reads.

THE ONE THING IT CANNOT DO — read this before trusting a size
An exported fixture pins EXISTENCE and POSITION exactly: a human looked at each shape
and said yes or no. It does NOT independently pin SIZE, because the sizes are the
pipeline's own measurements. A systematic sizing error (a wrong scale, the raster
ink-interior bias) is invisible to a fixture built this way — it would be baked into
the "truth" and the test would go green on it forever.

So exported entries are marked `"sizes": "measured"`. Where the drawing prints a
nominal size (Ø235 THRU), replace the number by hand and flip the marker to
"confirmed". Only then does the fixture defend the scale.

Run:
    uv run python tools/export_fixture.py --list
    uv run python tools/export_fixture.py --doc 7               # print to stdout
    uv run python tools/export_fixture.py --doc 7 --merge       # into ground_truth.json
    uv run python tools/export_fixture.py --all --merge
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

from shapely import wkt

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.bom.shapes import dims_key, shape_metrics  # noqa: E402
from app.db.models import Cutout, Document, Page  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from tools.eval_detection import reported_cutouts  # noqa: E402

TRUTH = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "ground_truth.json"


def _geometry(c: Cutout):
    """The shape as the operator left it — an edit replaces the detection."""
    return wkt.loads(c.edited_geometry_wkt or c.geometry_wkt)


def _cut_hint(c: Cutout) -> float | None:
    if not c.measured_dims_json:
        return None
    try:
        return json.loads(c.measured_dims_json).get("cut_length_mm")
    except (ValueError, AttributeError):
        return None


def check_stale(db, doc: Document) -> str | None:
    """Is this review still describing what the pipeline does today?

    A finalized document is a snapshot of one job run. Nothing marks it stale when the
    pipeline improves, and this is not theoretical: Doc_HK3573 was finalized on
    2026-07-16 with TWO REAL BOLT HOLES auto-rejected at 0.38 and 0.40, because the
    run predated the fixes. The current pipeline scores all 17 above the threshold.

    Exporting that document would write "13 bolt holes" into ground truth and make an
    old detector bug the definition of correct — the third time this file would have
    been wrong. So: re-run the current pipeline and refuse if the counts disagree.
    """
    src = Path(doc.path)
    if not src.exists():
        return f"source file is gone ({src}) — cannot check the review against today's pipeline"
    approved = (
        db.query(Cutout)
        .join(Page, Cutout.page_id == Page.id)
        .filter(Page.document_id == doc.id, Cutout.status == "approved")
        .count()
    )
    now = len(reported_cutouts(src, tag=f"doc{doc.id}"))
    if now != approved:
        return (
            f"STALE REVIEW: this document was finalized with {approved} approved cutout(s), "
            f"but the pipeline finds {now} above the threshold today. The review describes "
            f"an older pipeline. Re-run extraction and review it again before exporting — "
            f"otherwise an old bug becomes ground truth."
        )
    return None


def _entry(db, doc: Document) -> dict:
    pages = db.query(Page).filter(Page.document_id == doc.id).order_by(Page.index).all()
    unconfirmed = [p.index for p in pages if p.scale is None or not p.scale_confirmed]
    if unconfirmed:
        raise SystemExit(
            f"{doc.filename}: scale not confirmed on page(s) "
            f"{', '.join(str(i + 1) for i in unconfirmed)} — a fixture whose scale "
            f"nobody vouched for measures ink, not parts."
        )

    # groups keyed on (shape, snapped size) so 16 bolt holes are one row, not sixteen
    groups: dict[tuple, list[dict]] = defaultdict(list)
    forbidden: list[dict] = []
    scales = {p.index: p.scale for p in pages}

    for page in pages:
        cutouts = db.query(Cutout).filter(Cutout.page_id == page.id).all()
        for c in cutouts:
            poly = _geometry(c)
            if c.status == "rejected":
                x0, y0, x1, y1 = poly.bounds
                forbidden.append(
                    {
                        "page": page.index,
                        # fractional page coords: immune to DPI and render changes
                        "rect": [
                            round(x0 / page.width_pt, 4),
                            round(y0 / page.height_pt, 4),
                            round(x1 / page.width_pt, 4),
                            round(y1 / page.height_pt, 4),
                        ],
                        "was": c.kind,
                        "confidence": round(c.confidence, 2),
                    }
                )
                continue
            if c.status != "approved":
                continue  # still pending: nobody has vouched for it either way

            m = shape_metrics(poly, c.kind, _cut_hint(c))
            scale = scales[page.index]
            real = {k: round(v * scale, 2) for k, v in m["dims"].items()}
            cx, cy = poly.centroid.x, poly.centroid.y
            groups[(m["shape"], dims_key(real))].append(
                {"dims": real, "center_pt": [round(cx, 2), round(cy, 2)], "page": page.index}
            )

    cutouts_out = []
    for (shape, _), members in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        # the group's size is the mean of its members: measurement noise should not
        # decide which of sixteen identical holes defines the row
        keys = members[0]["dims"].keys()
        avg = {k: round(sum(m["dims"][k] for m in members) / len(members), 2) for k in keys}
        cutouts_out.append(
            {
                "shape": shape,
                **avg,
                "qty": len(members),
                "centers_pt": [m["center_pt"] for m in members],
                "source": f"exported from reviewed document {doc.id}",
            }
        )

    n_pages = len(pages)
    entry: dict = {
        "part": f"TODO - name the part ({doc.filename})",
        "scale": scales[0],
        # NOT independently confirmed: these are the pipeline's own measurements,
        # approved as REAL by a human but never re-measured against the drawing
        "sizes": "measured",
        "cutouts": cutouts_out,
    }
    if n_pages > 1:
        entry["page_scales"] = {str(i): s for i, s in scales.items()}
    if forbidden:
        entry["forbidden_regions"] = forbidden
    return entry


def _enrich(existing: dict, export: dict) -> dict:
    """Add positional facts to a hand-confirmed entry without changing its answer.

    Centres are grouped by SHAPE rather than aligned to individual rows: a positional
    test only needs "every circle we report is near some true circle", and matching
    export groups to hand-written rows one-to-one would be a guess.
    """
    out = dict(existing)
    centers: dict[str, list] = {}
    for c in export["cutouts"]:
        centers.setdefault(c["shape"], []).extend(c["centers_pt"])
    out["centers_pt"] = centers
    if export.get("forbidden_regions"):
        out["forbidden_regions"] = export["forbidden_regions"]
    out["centers_source"] = export["cutouts"][0]["source"] if export["cutouts"] else ""
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--doc", type=int, action="append", help="document id (repeatable)")
    ap.add_argument("--all", action="store_true", help="every finalized document")
    ap.add_argument("--list", action="store_true", help="show what is exportable")
    ap.add_argument("--merge", action="store_true", help="write into ground_truth.json")
    ap.add_argument(
        "--force",
        action="store_true",
        help="export a stale review / overwrite a hand-confirmed entry (say why in the commit)",
    )
    args = ap.parse_args()

    with SessionLocal() as db:
        if args.list:
            for d in db.query(Document).order_by(Document.id).all():
                n = (
                    db.query(Cutout)
                    .join(Page, Cutout.page_id == Page.id)
                    .filter(Page.document_id == d.id, Cutout.status == "approved")
                    .count()
                )
                mark = "READY" if d.status == "approved" else d.status
                print(f"  [{d.id:>3}] {mark:<10} {n:>4} approved  {d.filename}")
            return 0

        if args.all:
            docs = db.query(Document).filter(Document.status == "approved").all()
        elif args.doc:
            docs = [db.get(Document, i) for i in args.doc]
            missing = [i for i, d in zip(args.doc, docs) if d is None]
            if missing:
                raise SystemExit(f"no such document: {missing}")
        else:
            raise SystemExit("nothing to do: pass --doc, --all or --list")

        if not docs:
            print("no finalized documents — review and finalize one first")
            return 1

        exported = {}
        for d in docs:
            if d.status != "approved":
                print(f"skipping {d.filename}: not finalized ({d.status})")
                continue
            stale = check_stale(db, d)
            if stale and not args.force:
                print(f"\nREFUSING {d.filename}\n  {stale}\n  (--force overrides)")
                continue
            if stale:
                print(f"\nWARNING, forced: {d.filename}\n  {stale}")
            exported[d.filename] = _entry(db, d)

    if not exported:
        return 1

    if args.merge:
        truth = json.loads(TRUTH.read_text(encoding="utf-8"))
        # A hand-confirmed entry is worth more than a machine export: someone read the
        # drawing. Ground truth has been wrong twice already; it must never be silently
        # replaced by whatever the detector believed on the day a document was reviewed.
        confirmed = [
            k
            for k in exported
            if k in truth and truth[k].get("sizes") != "measured"
        ]
        blocked = []
        for k in confirmed:
            old = sum(c["qty"] for c in truth[k]["cutouts"])
            new = sum(c["qty"] for c in exported[k]["cutouts"])
            if old != new and not args.force:
                print(
                    f"\nREFUSING to overwrite hand-confirmed fixture {k}\n"
                    f"  existing (read off the drawing): {old} cutout(s)\n"
                    f"  this export (detector's belief): {new} cutout(s)\n"
                    f"  If the export is right, the drawing was mis-labelled — check it by "
                    f"eye, then --force."
                )
                blocked.append(k)
                continue
            if old == new and not args.force:
                # The review agrees with the confirmed answer, so the export adds no
                # opinion — only facts the hand-written entry never had: WHERE each
                # cutout is, and which rejected shapes are confirmed false positives.
                # Sizes stay as a human typed them; a measured size cannot defend a
                # scale error, and overwriting nominal Ø40 with measured Ø40.13 would
                # quietly retire the only check that can.
                enriched = _enrich(truth[k], exported.pop(k))
                truth[k] = enriched
                print(f"enriched {k}: added centres" + (
                    " and forbidden_regions" if "forbidden_regions" in enriched else ""
                ))
        if blocked:
            for k in blocked:
                exported.pop(k, None)
        overwritten = [k for k in exported if k in truth]
        truth.update(exported)
        TRUTH.write_text(
            json.dumps(truth, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(f"merged {len(exported)} entr(y/ies) into {TRUTH}")
        if overwritten:
            print(f"  OVERWROTE existing: {', '.join(overwritten)}")
    else:
        print(json.dumps(exported, indent=2, ensure_ascii=False))

    print(
        "\nSizes are the pipeline's OWN measurements (\"sizes\": \"measured\"). They pin "
        "existence and position, not size.\nWhere the drawing prints a nominal size, type "
        'it in and set "sizes": "confirmed" — until then this fixture\ncannot catch a scale '
        "error."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
