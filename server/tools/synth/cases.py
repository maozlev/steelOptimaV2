"""The case matrix: ~1000 synthetic PDF drawings with exact ground truth.

DESIGNED TO FAIL, NOT TO PASS
A corpus that goes green teaches nothing. Every family below sweeps its parameter INTO
the region where the pipeline's constants say behaviour must change, so the report is a
failure surface with the boundary located, not a pass rate.

TWO QUESTIONS, KEPT SEPARATE
  must_detect  - a candidate exists at all. This is Maoz's rule, "never miss a real
                 hole", and it is non-negotiable for every real opening.
  must_approve - it scores >= finalize_threshold (0.90) and reaches the BOM unattended.
                 Only asserted for the four ideal shapes. A freeform cutout carries a
                 -0.3 penalty by design and is surfaced for review instead; counting
                 that as a miss would invent failures that are correct behaviour.

WHAT THIS CANNOT DO
These drawings encode the drafting conventions I know about. They cannot contain the one
that breaks the pipeline next — the coloured-layer bug scored 100% on nine real drawings
right up until it was probed. Use this to stop regressions and to localise known breakage.
Real labelled drawings remain the only evidence about real accuracy.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable

import fitz
from shapely.geometry import Polygon

from app.extraction.vector import NOTCH_MIN_HOST_FRAC

from tools.synth.shapes import (
    CONVENTIONS,
    FREEFORM_KINDS,
    NOTCH_KINDS,
    circle_pts,
    freeform_pts,
    ngon_pts,
    notch_profile,
    obround_pts,
    pen,
    rect_pts,
    rotate,
    sheet_frame,
    stroke,
)

PAGE_W, PAGE_H = 595.0, 842.0
PART = fitz.Rect(90, 90, 505, 620)  # the plate every cutout is cut out of
PART_CX, PART_CY = (PART.x0 + PART.x1) / 2, (PART.y0 + PART.y1) / 2


@dataclass
class Expect:
    center: tuple[float, float]
    bbox: tuple[float, float]
    shape: str | None = None  # circle | rectangle | slot | notch | None = any
    must_detect: bool = True
    must_approve: bool = False


@dataclass
class Case:
    id: str
    family: str
    params: dict
    draw: Callable[[fitz.Page], None]
    expect: list[Expect] = field(default_factory=list)
    # rectangles (x0, y0, x1, y1) in page points where NOTHING may be reported
    forbid: list[tuple] = field(default_factory=list)
    note: str = ""


# ---------------------------------------------------------------- page furniture


def _dimension_lines(page: fitz.Page, conv: str) -> None:
    """Annotation ink. Present on every page: without it a colour-coded sheet has no
    annotation share and `ink.py` would fall through to the width convention instead."""
    p = pen(conv, "annotation")
    page.draw_line(fitz.Point(90, 660), fitz.Point(505, 660), **p)
    page.draw_line(fitz.Point(90, 650), fitz.Point(90, 670), **p)
    page.draw_line(fitz.Point(505, 650), fitz.Point(505, 670), **p)
    page.draw_line(fitz.Point(540, 90), fitz.Point(540, 620), **p)
    page.insert_text(fitz.Point(280, 655), "415", fontsize=9, fontname="helv")


def _plate(page: fitz.Page, conv: str) -> None:
    stroke(page, rect_pts(PART_CX, PART_CY, PART.width, PART.height), conv)


def _base(conv: str, inner: Callable[[fitz.Page], None], plate: bool = True):
    def draw(page: fitz.Page) -> None:
        sheet_frame(page, conv)
        if plate:
            _plate(page, conv)
        inner(page)
        _dimension_lines(page, conv)

    return draw


# ---------------------------------------------------------------- A: circles

CIRCLE_D = [2.5, 3, 4, 6, 10, 16, 25, 40, 63, 100]
POSITIONS = {
    "center": (PART_CX, PART_CY),
    "corner": (PART.x0 + 60, PART.y0 + 60),
    "edge": (PART.x1 - 45, PART_CY),
}


def circles() -> list[Case]:
    out = []
    for d in CIRCLE_D:
        for pos, (cx, cy) in POSITIONS.items():
            for conv in CONVENTIONS:
                out.append(
                    Case(
                        id=f"circle_d{d}_{pos}_{conv}",
                        family="circle",
                        params={"d_pt": d, "pos": pos, "conv": conv},
                        draw=_base(
                            conv,
                            lambda p, d=d, cx=cx, cy=cy, conv=conv: stroke(
                                p, circle_pts(cx, cy, d / 2), conv
                            ),
                        ),
                        expect=[
                            Expect((cx, cy), (d, d), "circle", must_approve=True)
                        ],
                    )
                )
    return out


# ---------------------------------------------------------------- B/C: rectangles, slots

ASPECTS = [1.0, 1.5, 2.0, 3.0, 5.0, 10.0, 20.0]
AREA_SCALES = {"small": 300.0, "medium": 2000.0, "large": 12000.0}
ANGLES = [0, 15, 30, 45, 60, 75]


def _len_wid(aspect: float, area: float) -> tuple[float, float]:
    w = math.sqrt(area / aspect)
    return w * aspect, w


def rectangles() -> list[Case]:
    out = []
    for aspect in ASPECTS:
        for sname, area in AREA_SCALES.items():
            length, width = _len_wid(aspect, area)
            if length > PART.width - 40 or width < 1.0:
                continue
            for ang in ANGLES:
                for conv in CONVENTIONS:
                    out.append(
                        Case(
                            id=f"rect_a{aspect}_{sname}_r{ang}_{conv}",
                            family="rectangle",
                            params={"aspect": aspect, "size": sname, "angle": ang, "conv": conv},
                            draw=_base(
                                conv,
                                lambda p, L=length, W=width, a=ang, conv=conv: stroke(
                                    p, rect_pts(PART_CX, PART_CY, L, W, a), conv
                                ),
                            ),
                            expect=[
                                Expect(
                                    (PART_CX, PART_CY), (length, width),
                                    "rectangle", must_approve=True,
                                )
                            ],
                        )
                    )
    return out


def slots() -> list[Case]:
    out = []
    for aspect in ASPECTS:
        if aspect < 1.5:
            continue  # an obround needs a straight section to be one
        for sname, area in AREA_SCALES.items():
            length, width = _len_wid(aspect, area)
            if length > PART.width - 40 or width < 2.0:
                continue
            for ang in ANGLES:
                for conv in CONVENTIONS:
                    out.append(
                        Case(
                            id=f"slot_a{aspect}_{sname}_r{ang}_{conv}",
                            family="slot",
                            params={"aspect": aspect, "size": sname, "angle": ang, "conv": conv},
                            draw=_base(
                                conv,
                                lambda p, L=length, W=width, a=ang, conv=conv: stroke(
                                    p, obround_pts(PART_CX, PART_CY, L, W, a), conv
                                ),
                            ),
                            expect=[
                                Expect(
                                    (PART_CX, PART_CY), (length, width),
                                    "slot", must_approve=True,
                                )
                            ],
                        )
                    )
    return out


# ---------------------------------------------------------------- D: polygons


def polygons() -> list[Case]:
    out = []
    for sides in (3, 5, 6, 7, 8, 10, 12):
        for r in (8.0, 20.0, 50.0):
            for ang in (0, 17):
                for conv in CONVENTIONS:
                    # a hex/oct bolt hole reads as a circle; a triangle fits no ideal
                    # shape at all, so only DETECTION is required of it
                    ideal = sides >= 6
                    out.append(
                        Case(
                            id=f"ngon{sides}_r{r}_a{ang}_{conv}",
                            family="polygon",
                            params={"sides": sides, "r_pt": r, "angle": ang, "conv": conv},
                            draw=_base(
                                conv,
                                lambda p, s=sides, r=r, a=ang, conv=conv: stroke(
                                    p, ngon_pts(PART_CX, PART_CY, r, s, a), conv
                                ),
                            ),
                            expect=[
                                Expect(
                                    (PART_CX, PART_CY), (2 * r, 2 * r),
                                    None, must_approve=ideal,
                                )
                            ],
                        )
                    )
    return out


# ---------------------------------------------------------------- E: freeform


def freeforms() -> list[Case]:
    out = []
    for kind in FREEFORM_KINDS:
        for size in (20.0, 45.0, 90.0):
            for ang in (0, 30):
                for conv in CONVENTIONS:
                    out.append(
                        Case(
                            id=f"free_{kind}_s{size}_a{ang}_{conv}",
                            family="freeform",
                            params={"kind": kind, "size": size, "angle": ang, "conv": conv},
                            draw=_base(
                                conv,
                                lambda p, k=kind, s=size, a=ang, conv=conv: stroke(
                                    p, freeform_pts(k, PART_CX, PART_CY, s, a), conv
                                ),
                            ),
                            # a real opening that fits no ideal shape: must be FOUND,
                            # is not expected to auto-approve
                            expect=[Expect((PART_CX, PART_CY), (size, size), None)],
                            note="freeform: detection required, approval not",
                        )
                    )
    return out


# ---------------------------------------------------------------- F: notches

NOTCH_DEPTH_FRACS = [0.004, 0.01, 0.03, 0.08, 0.16]


def notches() -> list[Case]:
    out = []
    w, h = PART.width, PART.height
    for kind in NOTCH_KINDS:
        for frac in NOTCH_DEPTH_FRACS:
            for notch_w in (40.0, 90.0):
                depth = frac * w * h / notch_w
                if depth > h * 0.5:
                    continue
                # How much of the part the bite actually removes. `frac` sets a
                # RECTANGULAR bite's area; a round-bottomed one of the same nominal
                # width and depth removes only ~pi/4 of that, so the nominal number
                # cannot decide whether it clears NOTCH_MIN_HOST_FRAC. Measure the
                # profile that will really be drawn.
                _prof = Polygon(notch_profile(kind, w, h, notch_w, depth))
                _bite = _prof.convex_hull.area - _prof.area
                _bite_frac = _bite / _prof.area if _prof.area else 0.0

                for conv in CONVENTIONS:
                    # a V bite is a chamfer — the part's own shape, never a cut. Below
                    # NOTCH_MIN_HOST_FRAC a bite is profile, not a manufactured feature.
                    # SHAPE FIT IS DELIBERATELY NOT CONSULTED HERE: it is the pipeline's
                    # mechanism, not ground truth. A round-bottomed notch is a real cut
                    # whatever it scores, and the corpus must be able to say so.
                    is_cut = kind != "vee" and _bite_frac >= NOTCH_MIN_HOST_FRAC
                    cx = PART.x0 + w / 2
                    cy = PART.y0 + h - depth / 2

                    def draw_inner(p, k=kind, nw=notch_w, d=depth, conv=conv):
                        pts = notch_profile(k, w, h, nw, d)
                        pts = [(PART.x0 + x, PART.y0 + y) for x, y in pts]
                        stroke(p, pts, conv)
                        # a part must CONTAIN something to be a part
                        stroke(p, circle_pts(PART.x0 + 70, PART.y0 + 70, 12), conv)

                    out.append(
                        Case(
                            id=f"notch_{kind}_f{frac}_w{notch_w}_{conv}",
                            family="notch",
                            params={
                                "kind": kind, "depth_frac": frac,
                                "notch_w": notch_w, "conv": conv,
                            },
                            draw=_base(conv, draw_inner, plate=False),
                            expect=(
                                [Expect((cx, cy), (notch_w, depth), "notch", must_approve=True)]
                                if is_cut
                                else []
                            )
                            + [Expect((PART.x0 + 70, PART.y0 + 70), (24, 24), "circle",
                                      must_approve=True)],
                            note="" if is_cut else "a chamfer / sub-threshold bite is not a cut",
                        )
                    )
    return out


# ---------------------------------------------------------------- G: patterns


def _bolt_circle(n: int, r: float, d: float):
    return [
        (PART_CX + r * math.cos(2 * math.pi * i / n), PART_CY + r * math.sin(2 * math.pi * i / n))
        for i in range(n)
    ]


def _grid(n: int, pitch: float):
    off = (n - 1) * pitch / 2
    return [
        (PART_CX - off + i * pitch, PART_CY - off + j * pitch)
        for i in range(n) for j in range(n)
    ]


PATTERNS = {
    "bolt4": lambda d: _bolt_circle(4, 140, d),
    "bolt8": lambda d: _bolt_circle(8, 140, d),
    "bolt16": lambda d: _bolt_circle(16, 160, d),
    "grid3": lambda d: _grid(3, 90),
    "grid5": lambda d: _grid(5, 55),
    "grid8": lambda d: _grid(8, 34),
    "row6": lambda d: [(PART_CX - 150 + i * 60, PART_CY) for i in range(6)],
    "stagger": lambda d: [
        (PART_CX - 120 + i * 60, PART_CY + (30 if i % 2 else -30)) for i in range(5)
    ],
}


def patterns() -> list[Case]:
    out = []
    for name, fn in PATTERNS.items():
        for d in (4.0, 12.0, 26.0):
            centres = fn(d)
            for conv in CONVENTIONS:
                out.append(
                    Case(
                        id=f"pat_{name}_d{d}_{conv}",
                        family="pattern",
                        params={"pattern": name, "d_pt": d, "n": len(centres), "conv": conv},
                        draw=_base(
                            conv,
                            lambda p, cs=centres, d=d, conv=conv: [
                                stroke(p, circle_pts(x, y, d / 2), conv) for x, y in cs
                            ],
                        ),
                        expect=[
                            Expect((x, y), (d, d), "circle", must_approve=True)
                            for x, y in centres
                        ],
                    )
                )
    return out


# ---------------------------------------------------------------- H: nested / bore ratio


def nested() -> list[Case]:
    out = []
    for frac in (0.3, 0.5, 0.7, 0.78, 0.85, 0.88, 0.92, 0.95):
        for part_r in (60.0, 120.0):
            bore_r = part_r * math.sqrt(frac)
            for conv in CONVENTIONS:
                # a gasket's bore fills 78% of its ring and is REAL; above
                # MAX_CUTOUT_PARENT_RATIO a "hole" is a double-stroked outline
                real = frac <= 0.90
                out.append(
                    Case(
                        id=f"bore_f{frac}_r{part_r}_{conv}",
                        family="nested",
                        params={"fill_frac": frac, "part_r": part_r, "conv": conv},
                        draw=_base(
                            conv,
                            lambda p, R=part_r, r=bore_r, conv=conv: (
                                stroke(p, circle_pts(PART_CX, PART_CY, R), conv),
                                stroke(p, circle_pts(PART_CX, PART_CY, r), conv),
                            ),
                            plate=False,
                        ),
                        expect=(
                            [Expect((PART_CX, PART_CY), (2 * bore_r, 2 * bore_r),
                                    "circle", must_approve=True)]
                            if real else []
                        ),
                        note="" if real else "above the parent-ratio cap: a restroke, not a bore",
                    )
                )
    return out


# ---------------------------------------------------------------- I: decoys

TITLE = (400.0, 700.0, 570.0, 800.0)


def _decoy_drawers() -> dict[str, Callable]:
    def title_block(p, conv):
        x0, y0, x1, y1 = TITLE
        for i in range(4):
            p.draw_line(fitz.Point(x0, y0 + i * 25), fitz.Point(x1, y0 + i * 25), **pen(conv))
        for i in range(3):
            p.draw_line(fitz.Point(x0 + i * 60, y0), fitz.Point(x0 + i * 60, y1), **pen(conv))

    def projection_symbol(p, conv):
        stroke(p, circle_pts(430, 730, 16), conv)
        stroke(p, circle_pts(430, 730, 9), conv)

    def fcf_frame(p, conv):
        stroke(p, rect_pts(480, 730, 60, 20), conv)
        stroke(p, circle_pts(460, 730, 6), conv)

    def callout_box(p, conv):
        stroke(p, rect_pts(480, 770, 70, 22), conv)
        p.insert_text(fitz.Point(455, 775), "605", fontsize=10, fontname="helv")

    def glyph_text(p, conv):
        p.insert_text(fitz.Point(430, 690), "O 290 THRU 8888", fontsize=14, fontname="helv")

    def rev_cloud(p, conv):
        for i in range(14):
            a = 2 * math.pi * i / 14
            stroke(p, circle_pts(470 + 40 * math.cos(a), 760 + 26 * math.sin(a), 9), conv)

    def gear_teeth(p, conv):
        pts = []
        for i in range(60):
            a = 2 * math.pi * i / 60
            r = 150 + (10 if i % 2 else 0)
            pts.append((PART_CX + r * math.cos(a), PART_CY + r * math.sin(a)))
        pts.append(pts[0])
        stroke(p, pts, conv)
        stroke(p, circle_pts(PART_CX, PART_CY, 30), conv)

    def chamfers(p, conv):
        c = 25
        pts = [
            (PART.x0 + c, PART.y0), (PART.x1 - c, PART.y0), (PART.x1, PART.y0 + c),
            (PART.x1, PART.y1 - c), (PART.x1 - c, PART.y1), (PART.x0 + c, PART.y1),
            (PART.x0, PART.y1 - c), (PART.x0, PART.y0 + c), (PART.x0 + c, PART.y0),
        ]
        stroke(p, pts, conv)
        stroke(p, circle_pts(PART_CX, PART_CY, 30), conv)

    def centre_lines(p, conv):
        pr = dict(pen(conv, "annotation"), dashes="[6 4] 0")
        p.draw_line(fitz.Point(PART.x0, PART_CY), fitz.Point(PART.x1, PART_CY), **pr)
        p.draw_line(fitz.Point(PART_CX, PART.y0), fitz.Point(PART_CX, PART.y1), **pr)
        stroke(p, circle_pts(PART_CX, PART_CY, 30), conv)

    def arrowheads(p, conv):
        for x in (200, 300, 400):
            p.draw_polyline(
                [fitz.Point(x, 660), fitz.Point(x + 8, 656), fitz.Point(x + 8, 664)],
                color=(0, 0, 0), fill=(0, 0, 0), width=0,
            )
        stroke(p, circle_pts(PART_CX, PART_CY, 30), conv)

    def detail_bubble(p, conv):
        # a BIG circle that is emphatically not a hole
        stroke(p, circle_pts(300, 720, 55), conv)
        p.insert_text(fitz.Point(292, 726), "A", fontsize=20, fontname="helv")
        stroke(p, circle_pts(PART_CX, PART_CY, 30), conv)

    def second_view(p, conv):
        # the same part drawn again in elevation: its hole must not be double-counted
        # as a second DISTINCT feature of the first view
        stroke(p, rect_pts(300, 720, 200, 60), conv)
        stroke(p, circle_pts(300, 720, 14), conv)
        stroke(p, circle_pts(PART_CX, PART_CY, 30), conv)

    def hatching(p, conv):
        pr = pen(conv, "annotation")
        for i in range(30):
            p.draw_line(fitz.Point(120 + i * 12, 120), fitz.Point(60 + i * 12, 300), **pr)
        stroke(p, circle_pts(PART_CX, PART_CY, 30), conv)

    def logo(p, conv):
        stroke(p, ngon_pts(120, 760, 30, 5), conv)
        stroke(p, circle_pts(120, 760, 12), conv)

    return {
        "title_block": title_block, "projection_symbol": projection_symbol,
        "fcf_frame": fcf_frame, "callout_box": callout_box, "glyph_text": glyph_text,
        "rev_cloud": rev_cloud, "gear_teeth": gear_teeth, "chamfers": chamfers,
        "centre_lines": centre_lines, "arrowheads": arrowheads,
        "detail_bubble": detail_bubble, "second_view": second_view,
        "hatching": hatching, "logo": logo,
    }


# decoys that live away from the plate: nothing may be reported in this band
OFF_PART = (380.0, 640.0, 595.0, 842.0)

DECOY_FORBID = {
    "title_block": [TITLE], "projection_symbol": [(400, 700, 470, 770)],
    "fcf_frame": [(440, 710, 520, 755)], "callout_box": [(435, 750, 525, 790)],
    "glyph_text": [(420, 675, 595, 700)], "rev_cloud": [(415, 720, 525, 800)],
    "detail_bubble": [(240, 660, 360, 780)], "logo": [(85, 725, 160, 800)],
    "second_view": [(195, 685, 405, 755)],
}


def decoys() -> list[Case]:
    out = []
    drawers = _decoy_drawers()
    for name, fn in drawers.items():
        for conv in CONVENTIONS:
            on_part = name in {"gear_teeth", "chamfers", "centre_lines", "arrowheads", "hatching"}
            expect = []
            if on_part or name in {"detail_bubble", "second_view", "logo",
                                   "projection_symbol", "fcf_frame", "callout_box",
                                   "glyph_text", "rev_cloud", "title_block"}:
                # every decoy page still carries ONE real hole, so a page that reports
                # nothing cannot be mistaken for a page that correctly rejected junk
                expect = [Expect((PART_CX, PART_CY), (60, 60), "circle", must_approve=True)]
            plate = name not in {"gear_teeth", "chamfers"}

            def with_real_hole(p, fn=fn, conv=conv):
                # every decoy page carries ONE real hole, so a page that reports nothing
                # cannot be scored the same as a page that correctly rejected the junk
                fn(p, conv)
                stroke(p, circle_pts(PART_CX, PART_CY, 30), conv)

            already = {
                "gear_teeth", "chamfers", "centre_lines", "arrowheads",
                "hatching", "detail_bubble", "second_view",
            }
            inner = (lambda p, fn=fn, conv=conv: fn(p, conv)) if name in already else with_real_hole
            out.append(
                Case(
                    id=f"decoy_{name}_{conv}",
                    family="decoy",
                    params={"decoy": name, "conv": conv},
                    draw=_base(conv, inner, plate=plate),
                    expect=expect,
                    forbid=DECOY_FORBID.get(name, []),
                    note="must not be reported as a cutout",
                )
            )
    return out


# ---------------------------------------------------------------- J: adversarial


def adversarial() -> list[Case]:
    out = []

    def two_touching(gap):
        def inner(p, conv, gap=gap):
            stroke(p, circle_pts(PART_CX - 20 - gap / 2, PART_CY, 20), conv)
            stroke(p, circle_pts(PART_CX + 20 + gap / 2, PART_CY, 20), conv)

        return inner, [
            Expect((PART_CX - 20 - gap / 2, PART_CY), (40, 40), "circle", must_approve=True),
            Expect((PART_CX + 20 + gap / 2, PART_CY), (40, 40), "circle", must_approve=True),
        ]

    for gap in (0.5, 2.0, 10.0):
        inner, exp = two_touching(gap)
        for conv in CONVENTIONS:
            out.append(Case(
                id=f"adv_touching_g{gap}_{conv}", family="adversarial",
                params={"case": "touching", "gap_pt": gap, "conv": conv},
                draw=_base(conv, lambda p, inner=inner, conv=conv: inner(p, conv)),
                expect=exp,
            ))

    for inset in (30.0, 10.0, 3.0):
        for conv in CONVENTIONS:
            cx = PART.x1 - inset - 8
            out.append(Case(
                id=f"adv_near_edge_i{inset}_{conv}", family="adversarial",
                params={"case": "near_edge", "inset_pt": inset, "conv": conv},
                draw=_base(conv, lambda p, cx=cx, conv=conv: stroke(
                    p, circle_pts(cx, PART_CY, 8), conv)),
                expect=[Expect((cx, PART_CY), (16, 16), "circle", must_approve=True)],
            ))

    for d in (3.0, 6.0):
        for conv in CONVENTIONS:
            out.append(Case(
                id=f"adv_tiny_in_big_d{d}_{conv}", family="adversarial",
                params={"case": "tiny_in_big", "d_pt": d, "conv": conv},
                draw=_base(conv, lambda p, d=d, conv=conv: stroke(
                    p, circle_pts(PART_CX, PART_CY, d / 2), conv)),
                expect=[Expect((PART_CX, PART_CY), (d, d), "circle", must_approve=True)],
                note="a 3pt hole on an A4 plate is a real 4mm hole at 1:5",
            ))

    for conv in CONVENTIONS:  # a hole drawn OUTSIDE the part is on the paper
        out.append(Case(
            id=f"adv_outside_part_{conv}", family="adversarial",
            params={"case": "outside_part", "conv": conv},
            draw=_base(conv, lambda p, conv=conv: (
                stroke(p, circle_pts(PART_CX, PART_CY, 25), conv),
                stroke(p, circle_pts(545, 750, 14), conv),
            )),
            expect=[Expect((PART_CX, PART_CY), (50, 50), "circle", must_approve=True)],
            forbid=[(515, 720, 575, 780)],
        ))

    for offset in (0.0, 0.3, 1.0):  # a double-stroked outline is not two holes
        for conv in CONVENTIONS:
            out.append(Case(
                id=f"adv_double_stroke_o{offset}_{conv}", family="adversarial",
                params={"case": "double_stroke", "offset_pt": offset, "conv": conv},
                draw=_base(conv, lambda p, o=offset, conv=conv: (
                    stroke(p, circle_pts(PART_CX, PART_CY, 30), conv),
                    stroke(p, circle_pts(PART_CX, PART_CY, 30 + o), conv),
                )),
                expect=[Expect((PART_CX, PART_CY), (60, 60), "circle", must_approve=True)],
                note="one hole, drawn twice",
            ))

    for n_paths in (1, 4, 12):  # a contour split across separate paths
        for conv in CONVENTIONS:
            def inner(p, n=n_paths, conv=conv):
                pts = circle_pts(PART_CX, PART_CY, 30, 48)
                per = len(pts) // n
                for i in range(n):
                    seg = pts[i * per: (i + 1) * per + 1] if i < n - 1 else pts[i * per:]
                    if len(seg) >= 2:
                        p.draw_polyline([fitz.Point(*q) for q in seg], **pen(conv))

            out.append(Case(
                id=f"adv_split_contour_n{n_paths}_{conv}", family="adversarial",
                params={"case": "split_contour", "n_paths": n_paths, "conv": conv},
                draw=_base(conv, inner),
                expect=[Expect((PART_CX, PART_CY), (60, 60), "circle", must_approve=True)],
            ))

    return out


# ---------------------------------------------------------------- K: size extremes


def size_extremes() -> list[Case]:
    out = []
    for pw, ph, label in ((595, 842, "A4"), (842, 1191, "A3"), (2384, 3370, "A0")):
        for d in (6.0, 20.0, 60.0):
            for conv in CONVENTIONS:
                sx, sy = pw / 2, ph / 2
                pw_, ph_ = pw * 0.7, ph * 0.6

                def draw(page, sx=sx, sy=sy, pw_=pw_, ph_=ph_, d=d, conv=conv):
                    stroke(page, rect_pts(sx, sy, pw_, ph_), conv)
                    stroke(page, circle_pts(sx, sy, d / 2), conv)
                    p = pen(conv, "annotation")
                    page.draw_line(fitz.Point(sx - pw_ / 2, sy + ph_ / 2 + 30),
                                   fitz.Point(sx + pw_ / 2, sy + ph_ / 2 + 30), **p)

                out.append(Case(
                    id=f"sheet_{label}_d{d}_{conv}", family="sheet_size",
                    params={"sheet": label, "d_pt": d, "conv": conv},
                    draw=draw,
                    expect=[Expect((sx, sy), (d, d), "circle", must_approve=True)],
                    note=f"page {pw}x{ph}pt",
                ))
    return out


PAGE_SIZE_BY_CASE = {
    "A4": (595, 842), "A3": (842, 1191), "A0": (2384, 3370),
}


def all_cases() -> list[Case]:
    cases: list[Case] = []
    for fn in (
        circles, rectangles, slots, polygons, freeforms, notches,
        patterns, nested, decoys, adversarial, size_extremes,
    ):
        cases += fn()
    return cases
