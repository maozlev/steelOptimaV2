"""Recover the sheet scale, so the BOM reports real millimetres instead of paper ones.

Every dimension the extractor measures is in PAPER mm. On a 1:3.5 sheet the gear's Ø290
bore measures 82.9mm of paper. Reporting that as the hole size is the most dangerous bug
this system can have: it silently produces parts of the wrong size.

The label "624" is not merely *near* the flange's width — it is written on the dimension
LINE that spans it, and that line is exactly 624mm of reality long. So:

    scale = label value / length of the dimension line it sits on

which is self-checking, because a sheet has one scale and every dimension line on it
must agree. Diameter callouts (Ø290 on a leader pointing at a bore) are read too, against
the bore they point at.

The scale printed on the sheet is used only to CROSS-CHECK, never alone, because it lies:
ASH-071222's sheet says "Scale 1:3.5" while its own title block says "SCALE:1:5", and
117-626-141_4's sheet says 1:2 while its block says 1:1. The blocks are stale template
defaults. A disagreement flags the page for review instead of silently picking a winner.

scale = real_mm / paper_mm.  A 1:5 sheet has scale 5.0; a 2:1 magnified sheet has 0.5.
"""

import re
import statistics
from dataclasses import dataclass, field

import fitz
from shapely.geometry import LineString, Point

from app.extraction.ink import split_ink
from app.extraction.vector import PT_TO_MM, Candidate, _item_points

# "Scale 1:5", "SCALE:2:1", "Scale 1:3.5"
SCALE_TEXT = re.compile(r"scale\s*:?\s*(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)", re.I)
# a bare "1:5" token, to be matched to a nearby "Scale" label by position
RATIO_TOKEN = re.compile(r"^(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)$")
# how far from the word "Scale" its value may sit, in page points
SCALE_LABEL_REACH_PT = 90.0

# A dimension label: an optional diameter mark, then a number. The diameter glyph is
# frequently not text at all (it is drawn as vector paths) and some exporters mangle it
# to U+FFFD, so it is optional — what the label points at tells us what it measures.
DIM_LABEL = re.compile(r"^[⌀ØΦ�¢]?\s*(\d+(?:[.,]\d+)?)$")
DIAMETER_MARK = re.compile(r"^[⌀ØΦ�¢]")

# A label is written ON its dimension line. Extension lines, ticks and arrowheads are
# short; a dimension line spans the thing it measures.
MAX_LABEL_TO_LINE_PT = 36.0
# ...but on a big sheet everything is bigger, including that gap. The label's own text
# height is the only ruler that travels: a dimension label sits within a few text-heights
# of the line it belongs to, on an A3 and on a 2.5-metre plot alike.
LABEL_REACH_IN_TEXT_HEIGHTS = 3.0
MIN_DIMENSION_LINE_PT = 20.0
# a Ø-marked callout may sit some way from the bore it points at
MAX_CALLOUT_DISTANCE_PT = 400.0
# an unmarked number is only read as a diameter if it is close to the bore, measured in
# multiples of that bore's own size — otherwise every number on the sheet would "point
# at" every hole (A (4) has 293 of them)
CALLOUT_REACH = 4.0
# with no printed scale to cross-check, this many labels must independently agree
MIN_AGREEING_LABELS = 3
# a leader line touches its label at one end and the feature it names at the other
LEADER_ATTACH_PT = 40.0
LEADER_TIP_PT = 12.0

# ratios this close to each other are the same scale
RATIO_AGREEMENT = 0.02
CROSS_CHECK_TOLERANCE = 0.02
MIN_SCALE, MAX_SCALE = 0.05, 200.0


@dataclass
class ScaleResult:
    scale: float | None
    source: str  # geometry | text | none
    confident: bool
    text_scales: list[float] = field(default_factory=list)
    geometry_ratios: list[float] = field(default_factory=list)
    note: str = ""


def parse_scale_text(page: fitz.Page) -> list[float]:
    """Every "N:M" printed on the sheet, as real/paper ratios.

    Matched SPATIALLY, not by regex over the flattened text. In Doc_HK3573's title block
    the word "Scale" is token 10 and its value "1:5" is token 99 — adjacent on the page,
    nowhere near each other in the PDF's text stream. Reading the text as one string, the
    drawing appeared to print no scale at all, and the resolver fell back to an
    unconfident guess of 3.149 for a sheet that says 1:5 in plain sight.

    Several results usually means the sheet and its title block contradict each other.
    """
    out = []
    for num, den in SCALE_TEXT.findall(page.get_text()):
        n, d = float(num), float(den)
        if n and d:
            out.append(d / n)  # "1:5" -> one of paper is five of reality

    # a bare "N:M" token sitting beside the word "Scale"
    words = page.get_text("words")
    labels = [w for w in words if w[4].strip().lower().startswith("scale")]
    for x0, y0, x1, y1, text, *_ in words:
        m = RATIO_TOKEN.match(text.strip())
        if not m:
            continue
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        near = any(
            abs(cx - (lx0 + lx1) / 2) <= SCALE_LABEL_REACH_PT
            and abs(cy - (ly0 + ly1) / 2) <= SCALE_LABEL_REACH_PT
            for lx0, ly0, lx1, ly1, *_ in labels
        )
        if near:
            n, d = float(m.group(1)), float(m.group(2))
            if n and d:
                out.append(d / n)
    return out


def _straight_segments(page: fitz.Page, paths: list[dict]) -> list[LineString]:
    """Every straight edge in these paths.

    Each consecutive pair of points, not just two-point items: a dimension line is often
    one polyline carrying its extension ticks and arrowheads, so looking only at 2-point
    items found nothing at all on most sheets.
    """
    m = page.rotation_matrix
    segs = []
    for path in paths:
        for item in path["items"]:
            pts = _item_points(item, m)
            for a, b in zip(pts, pts[1:]):
                if a != b:
                    segs.append(LineString([a, b]))
    return segs


def _annotation_lines(page: fitz.Page) -> tuple[list[LineString], list[LineString]]:
    """(dimension lines, leaders) drawn in annotation ink.

    Falls back to all ink when a page draws its dimensions in the same colour as the
    part — A (4) does, and would otherwise offer nothing to measure a scale from.
    """
    _geometry, annotation = split_ink(page)
    segs = _straight_segments(page, annotation)
    if not segs:
        segs = _straight_segments(page, page.get_drawings())
    dimension = [s for s in segs if s.length >= MIN_DIMENSION_LINE_PT]
    return dimension, segs


def _leader_target(
    at: Point, leaders: list[LineString], candidates: list[Candidate]
) -> Candidate | None:
    """The feature a callout points at, by following its leader line.

    A diameter callout is joined to its bore by a leader: one end touches the label, the
    other touches the geometry. Following it beats guessing by proximity — the washer's
    "Ø75" sits right across the sheet from the Ø75 bore it names.
    """
    # only real features: a leader that happens to graze one of the freeform faces its
    # own lines carve out would otherwise "name" that artifact
    targets = [c for c in candidates if c.kind != "freeform"]
    for seg in leaders:
        ends = [Point(seg.coords[0]), Point(seg.coords[-1])]
        near_label = [e for e in ends if e.distance(at) <= LEADER_ATTACH_PT]
        if not near_label:
            continue
        tip = ends[1] if near_label[0] == ends[0] else ends[0]
        for c in targets:
            if c.polygon.distance(tip) <= LEADER_TIP_PT:
                return c
    return None


def _labels(page: fitz.Page) -> list[tuple[float, bool, Point, float]]:
    """(value, is_diameter, position, text height) for every number on the sheet.

    The text height comes back because it is the only page-size-independent ruler we
    have. A (3) is plotted on a 2540x1504mm sheet — six times the size of the A3s — and
    everything on it, including the gap between a dimension label and its line, is
    proportionally larger. A fixed 36pt tolerance threw away 7128, 2857, 3397 and 300 as
    "too far from any line", leaving one mismatched label to invent a scale from.
    """
    out = []
    for x0, y0, x1, y1, text, *_ in page.get_text("words"):
        t = text.strip()
        m = DIM_LABEL.match(t)
        if m:
            out.append(
                (
                    float(m.group(1).replace(",", ".")),
                    bool(DIAMETER_MARK.match(t)),
                    Point((x0 + x1) / 2, (y0 + y1) / 2),
                    max(y1 - y0, 1.0),
                )
            )
    return out


def _hole_diameters_pt(candidates: list[Candidate]) -> list[tuple[float, Candidate]]:
    return [
        (c.measured_dims["diameter_mm"] / PT_TO_MM, c)
        for c in candidates
        if "diameter_mm" in c.measured_dims and c.measured_dims["diameter_mm"] > 0
    ]


def infer_from_geometry(page: fitz.Page, candidates: list[Candidate]) -> list[float]:
    """Every ratio a label could plausibly imply, from either reading of it.

    A number on a drawing is either written on a dimension line (and measures its span)
    or is a diameter callout on a leader (and measures the bore it points at). We cannot
    always tell which: the Ø glyph is usually drawn as vector paths rather than text, so
    "Ø290 THRU" reaches us as the bare word "290". So both readings are offered and the
    consensus decides — a sheet has one scale, and the wrong reading of a label agrees
    with nobody.

    Sheet-border grid labels (the 1-8 / A-F around an A3 frame) fall out for free: they
    sit on frame ink, not on a dimension line, and point at no geometry.
    """
    lines, leaders = _annotation_lines(page)
    holes = _hole_diameters_pt(candidates)
    ratios: list[float] = []

    def keep(r: float) -> None:
        if MIN_SCALE <= r <= MAX_SCALE:
            ratios.append(r)

    for value, is_diameter, at, text_h in _labels(page):
        # How far a label may sit from its line scales with the drawing: the label's own
        # text height is the ruler. A (3) is plotted on a 2.5-metre sheet, where the gap
        # is 54-95pt; an A3 sheet's is under 20. A constant threw the big sheet away.
        reach = max(MAX_LABEL_TO_LINE_PT, text_h * LABEL_REACH_IN_TEXT_HEIGHTS)

        # reading 1: written on the dimension line that spans what it measures
        near_lines = [line for line in lines if line.distance(at) <= reach]
        if near_lines:
            line = max(near_lines, key=lambda ln: ln.length)
            keep(value / (line.length * PT_TO_MM))

        # reading 2: a callout, followed down its leader to the feature it names
        target = _leader_target(at, leaders, candidates)
        if target is not None:
            for dim_mm in target.measured_dims.values():
                if dim_mm > 0:
                    keep(value / dim_mm)

        # reading 3: a diameter callout with no leader we could follow, sitting close
        # enough to a bore (in multiples of that bore's own size) to be naming it
        if holes:
            dia_pt, hole = min(holes, key=lambda h: h[1].polygon.distance(at))
            dist = hole.polygon.distance(at)
            limit = MAX_CALLOUT_DISTANCE_PT if is_diameter else dia_pt * CALLOUT_REACH
            if dist <= limit and dia_pt > 0:
                keep(value / (dia_pt * PT_TO_MM))

    return ratios


def _consensus(ratios: list[float]) -> list[float]:
    """The largest set of ratios that agree. A sheet has one scale, so the truth is
    what many dimension lines independently say; a mismatched label agrees with nobody."""
    best: list[float] = []
    for pivot in ratios:
        group = [r for r in ratios if abs(r - pivot) <= RATIO_AGREEMENT * pivot]
        if len(group) > len(best):
            best = group
    return best


def resolve_scale(page: fitz.Page, candidates: list[Candidate]) -> ScaleResult:
    """The printed scale proposes; the drawing's own geometry confirms or refutes it.

    Neither source is trusted alone. A title block is a stale template default and lies
    (ASH prints 1:5 on a 1:3.5 sheet). Geometry alone can be fooled by a label matched to
    the wrong feature. Where a printed scale is independently backed by the dimensions,
    that is as certain as this gets; where nothing backs it, the page is flagged rather
    than guessed.
    """
    text_scales = parse_scale_text(page)
    ratios = infer_from_geometry(page, candidates)

    # A printed scale that the drawing's own dimensions independently reproduce.
    supported = [
        t
        for t in dict.fromkeys(text_scales)
        if any(abs(r - t) <= CROSS_CHECK_TOLERANCE * t for r in ratios)
    ]
    if len(supported) == 1:
        rejected = [t for t in dict.fromkeys(text_scales) if t not in supported]
        return ScaleResult(
            supported[0],
            "geometry",
            True,
            text_scales,
            ratios,
            f"printed scale {rejected} contradicted by the drawing's own dimensions — "
            "stale title block, ignored"
            if rejected
            else "",
        )

    agreed = _consensus(ratios)
    geometric = statistics.median(agreed) if agreed else None

    if len(supported) > 1:
        return ScaleResult(
            None,
            "none",
            False,
            text_scales,
            ratios,
            f"the sheet prints several scales {supported} and the geometry backs more "
            "than one — needs a human",
        )

    if geometric is not None:
        if text_scales:
            return ScaleResult(
                geometric,
                "geometry",
                False,
                text_scales,
                agreed,
                f"sheet prints {text_scales} but its own dimensions say "
                f"{geometric:.3f} — trusting the geometry, flagged for review",
            )
        # nothing printed: believe the geometry only when several labels agree
        enough = len(agreed) >= MIN_AGREEING_LABELS
        return ScaleResult(
            geometric,
            "geometry",
            enough,
            text_scales,
            agreed,
            "" if enough else f"only {len(agreed)} label(s) agree on this scale",
        )

    if len(set(text_scales)) == 1:
        return ScaleResult(
            text_scales[0],
            "text",
            False,
            text_scales,
            note="printed scale only — nothing on the drawing verifies it",
        )
    if text_scales:
        return ScaleResult(
            None,
            "none",
            False,
            text_scales,
            note=f"sheet prints contradictory scales {text_scales}, nothing breaks the tie",
        )
    return ScaleResult(
        None, "none", False, note="no printed scale and nothing to measure one from"
    )
