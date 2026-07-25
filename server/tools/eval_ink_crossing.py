"""Reproduce A5.png's missing bolt holes on demand, with the cause as a dial.

THE PROBLEM THIS EXISTS FOR
A5.png finds 13 of its 16 bolt holes. The three misses are diagnosed: a leader arrow and
the "16x 22.5=360" dimension arc are drawn straight THROUGH them, shattering each hole's
interior into fragments too small to re-fuse. That is backlog item 2 and a whole failure
class on real scans — and the entire repo contains exactly THREE examples of it.

Three examples is not a dataset. You cannot tell whether a fix works, only whether it
happens to rescue those three, which is overfitting with extra steps. So this generates
the same failure with every cause parameterised: crossing angle, how far the line passes
from the hole's centre, how thick it is, how big the hole is, and at what DPI the sheet
was scanned. That is a recall SURFACE instead of an anecdote.

WHAT IT IS NOT
These are clean synthetic renders. They carry none of a real scanner's noise, skew, JPEG
ringing or paper texture, so a fix that works here is necessary, not sufficient — confirm
against A5.png (`tools/eval_detection.py`) before believing it. What they do reproduce is
the geometric mechanism: ink crossing a hole's boundary and cutting its interior in two.

Run:
    uv run python tools/eval_ink_crossing.py                 # the default sweep
    uv run python tools/eval_ink_crossing.py --dpi 300 600
    uv run python tools/eval_ink_crossing.py --keep <dir>    # also write the PNGs out
"""

from __future__ import annotations

import argparse
import math
import sys
import tempfile
from pathlib import Path

import fitz

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.eval_detection import reported_cutouts  # noqa: E402

# A5.png's bolt holes are Ø12.5mm on a 1:5.03 sheet — about 7pt of paper. That size is
# the whole point: a big bore survives being crossed, a bolt hole does not.
BOLT_HOLE_PT = 7.0

PLATE = fitz.Rect(80, 80, 400, 320)
HOLE_CENTER = fitz.Point(240, 200)


def build_page(
    hole_d_pt: float,
    angle_deg: float | None,
    offset_frac: float,
    line_w: float,
    doc: fitz.Document,
) -> None:
    """One plate, one hole, and optionally one annotation line drawn across it.

    `offset_frac` is the line's perpendicular distance from the hole's centre as a
    fraction of its RADIUS: 0.0 splits the hole in half, 1.0 grazes the rim, and
    beyond 1.0 the line misses entirely.
    """
    page = doc.new_page(width=595, height=842)
    page.draw_rect(PLATE, color=(0, 0, 0), width=1.0)
    page.draw_circle(HOLE_CENTER, hole_d_pt / 2, color=(0, 0, 0), width=1.0)

    if angle_deg is None:
        return

    r = hole_d_pt / 2
    a = math.radians(angle_deg)
    dx, dy = math.cos(a), math.sin(a)
    # perpendicular offset, so the line passes `offset_frac * r` from the centre
    px, py = -dy * offset_frac * r, dx * offset_frac * r
    reach = 160.0
    page.draw_line(
        (HOLE_CENTER.x + px - dx * reach, HOLE_CENTER.y + py - dy * reach),
        (HOLE_CENTER.x + px + dx * reach, HOLE_CENTER.y + py + dy * reach),
        color=(0, 0, 0),
        width=line_w,
    )


def hole_found(
    hole_d_pt: float,
    angle_deg: float | None,
    offset_frac: float,
    line_w: float,
    dpi: int,
    out_dir: Path,
    tag: str,
) -> bool:
    """Render the case as a scan and ask the raster pipeline what it finds."""
    doc = fitz.open()
    build_page(hole_d_pt, angle_deg, offset_frac, line_w, doc)
    png = out_dir / f"{tag}.png"
    doc[0].get_pixmap(dpi=dpi).save(png)
    doc.close()

    # a hole is "found" if something round of roughly the right size is reported near
    # where we drew it — not merely if the count is right
    want_mm = hole_d_pt * 25.4 / 72
    for m in reported_cutouts(png, tag=tag):
        if m["shape"] != "circle":
            continue
        got = m["dims"].get("diameter_mm", 0)
        # raster measures the ink INTERIOR, so a hole reads systematically small
        if 0.5 * want_mm <= got <= 1.5 * want_mm:
            return True
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dpi", type=int, nargs="+", default=[300])
    ap.add_argument("--hole-pt", type=float, nargs="+", default=[BOLT_HOLE_PT, 20.0, 60.0])
    ap.add_argument("--angles", type=float, nargs="+", default=[0, 30, 45, 60, 90])
    ap.add_argument("--offsets", type=float, nargs="+", default=[0.0, 0.4, 0.8, 1.2])
    ap.add_argument("--widths", type=float, nargs="+", default=[0.35, 0.7, 1.4])
    ap.add_argument("--keep", type=Path, help="write the PNGs here instead of a temp dir")
    args = ap.parse_args()

    out_dir = args.keep or Path(tempfile.mkdtemp(prefix="inkcross_"))
    out_dir.mkdir(parents=True, exist_ok=True)

    total = lost = 0
    for dpi in args.dpi:
        for d in args.hole_pt:
            clean = hole_found(d, None, 0, 0, dpi, out_dir, f"clean_{d}_{dpi}")
            print(f"\n=== hole {d}pt @ {dpi} DPI — uncrossed baseline: "
                  f"{'FOUND' if clean else 'LOST (nothing below is meaningful)'}")
            if not clean:
                continue
            print(f"{'width':>7} {'offset':>7}  " + "  ".join(f"{a:>4.0f}deg" for a in args.angles))
            for w in args.widths:
                for off in args.offsets:
                    row = []
                    for ang in args.angles:
                        tag = f"x_{d}_{dpi}_{w}_{off}_{ang}"
                        ok = hole_found(d, ang, off, w, dpi, out_dir, tag)
                        total += 1
                        lost += not ok
                        row.append(" ok  " if ok else " LOST")
                    print(f"{w:>7} {off:>7}  " + "  ".join(f"{c:>7}" for c in row))

    print(f"\n{lost}/{total} crossed cases lose the hole entirely.")
    print(f"PNGs in {out_dir}")
    print(
        "\nA hole marked LOST is one a scan would silently drop from the BOM. Every 'ok' "
        "is a case a\nfix must not break; every LOST is one it has to win. Confirm any "
        "fix against A5.png too —\nthese renders have no scanner noise."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
