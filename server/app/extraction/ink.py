"""Decide HOW TO READ a drawing, then separate part geometry from annotation.

This is where the pipeline chooses which of the drafting conventions a page follows. Get
it wrong and everything downstream is garbage, so the decision is made from evidence on
the page, never from the file name, the part, or how "simple" the drawing looks.

WHY IT EXISTS
The extractor used to polygonize every stroke on the page — part edges, leader lines,
arrowheads and glyph outlines alike — and then try to sort cutouts back out by shape.
That cannot work: the `Ø` in a label "Ø290 THRU" is drawn as vector paths and IS,
geometrically, a circle. On ASH-071222 it was auto-approved at 0.98 as a Ø3.1 hole while
the drawing's only real bore was rejected. No threshold separates a glyph from a hole,
because they are the same shape. But the draughtsman already marked which ink is which.

THE DECISION TREE  (split_ink)

    1. COLOUR.  Classify every stroked path by max(r,g,b):
           < 0.4   -> geometry    part edges (black, or the 0.25 grey A (3) uses)
          >= 0.6   -> frame       the sheet border, discarded
           else    -> annotation  dimension/leader lines (0.5 grey, or olive)
       Fill-only paths (arrowheads, solid symbols) are always annotation.

    2. Is the page ACTUALLY colour-coded?  Only if a meaningful share of its STROKES land
       in annotation (>= MIN_ANNOTATION_SHARE). This test is the whole ballgame: Doc_HK3573
       has exactly ONE coloured stroke on the sheet — a highlight box — and an earlier
       version of this check let that single path convince it the page was colour-coded,
       silently disabling everything below. I then swept the width threshold across four
       values, got four identical results, and reported that width separation "did not
       help". It was never switched on. If a sweep gives identical results at every
       setting, the knob is not connected.

    3. WIDTH.  If colour says nothing, fall back to stroke width. ISO drafting draws
       visible part edges THICK and dimension / extension / leader / centre lines THIN.
       Doc_HK3573 is wholly black: 378 paths at 0.36 against 81 at 0.72-1.44. The cut is
       the thinnest width present x THIN_TO_THICK_RATIO.

    4. FAIL SAFE.  If the page follows neither convention, treat all non-frame ink as
       geometry. We are then exactly where we were before this module existed — noisier,
       but never blind. NEVER let this filter reduce a page to nothing: a missed hole
       costs a part, a false positive costs a click.

Roughly half the sample drawings take each path. `tools/inspect_ink.py` prints which one a
given drawing takes and why — run it first when a new drawing behaves oddly.

WHAT THIS IS NOT
It is not a classifier of "simple vs complex" drawings, and it must not become one. The
axis that matters is the CONVENTION, which is measurable; complexity is not, and the two
do not line up — A (3) and A (4) share their convention with the washer and the gasket.
Adding support for a new drafting house means adding a DETECTOR here, not a second
pipeline.

Annotation paths are returned, not discarded: scale.py measures the sheet scale from the
dimension lines.
"""

import fitz

GEOMETRY = "geometry"
ANNOTATION = "annotation"
FRAME = "frame"

# max(r,g,b): 0.0 is black, 1.0 is white. Part lines are drawn dark; dimension and
# leader lines mid-grey or a muted colour; the sheet border lighter still.
GEOMETRY_MAX_CHANNEL = 0.4
FRAME_MIN_CHANNEL = 0.6

# a page with less than this share of dark ink is not using the convention
MIN_GEOMETRY_SHARE = 0.05

# ISO drafting also separates the layers by stroke WIDTH: visible part edges are drawn
# thick, dimension / extension / leader / centre lines thin. Used when colour says
# nothing — Doc_HK3573 is wholly black, 378 thin paths against 81 thick ones.
THIN_TO_THICK_RATIO = 1.2
MIN_GEOMETRY_WIDTH = 0.4
# a stray coloured stroke or two does not mean the page uses the colour convention
MIN_ANNOTATION_SHARE = 0.05



def classify_path(path: dict) -> str:
    """geometry | annotation | frame, from stroke colour."""
    color = path.get("color")
    if color is None:  # fill-only: arrowheads, solid symbols
        return ANNOTATION
    level = max(color)
    if level < GEOMETRY_MAX_CHANNEL:
        return GEOMETRY
    if level >= FRAME_MIN_CHANNEL:
        return FRAME
    return ANNOTATION


def split_by_width(paths: list[dict]) -> tuple[list[dict], list[dict]]:
    """Separate by stroke width, for a page drawn entirely in one colour.

    ISO drafting draws visible part edges THICK and dimension / extension / leader /
    centre lines THIN. Doc_HK3573 is wholly black — 378 paths at 0.36 wide against 81 at
    0.72-1.44 — so colour says nothing and only the width does. Its thin extension lines
    are what slice the ring into the crescent faces that were reaching the BOM.
    """
    widths = sorted({round(p["width"], 2) for p in paths if p.get("width")})
    if len(widths) < 2:
        return paths, []
    cut = max(widths[0] * THIN_TO_THICK_RATIO, MIN_GEOMETRY_WIDTH)
    geometry = [p for p in paths if (p.get("width") or 0) >= cut]
    annotation = [p for p in paths if 0 < (p.get("width") or 0) < cut]
    return (geometry, annotation) if geometry and annotation else (paths, [])


def split_ink(page: fitz.Page) -> tuple[list[dict], list[dict]]:
    """(geometry paths, annotation paths) for one page.

    Annotation paths are returned rather than discarded: dimension lines and their
    leaders are what `scale.py` measures the sheet scale from.
    """
    paths = page.get_drawings()
    geometry = [p for p in paths if classify_path(p) == GEOMETRY]
    annotation = [p for p in paths if classify_path(p) == ANNOTATION]

    # Does this page actually use the colour convention? Fill-only paths (arrowheads) are
    # always annotation, and a stray coloured stroke proves nothing — Doc_HK3573 has
    # exactly ONE, a highlight box, and it was enough to make this look like a
    # colour-coded sheet and suppress the width fallback entirely.
    strokes = [p for p in paths if p.get("color") is not None]
    coloured = [p for p in strokes if classify_path(p) == ANNOTATION]
    if strokes and len(coloured) < MIN_ANNOTATION_SHARE * len(strokes):
        by_w = split_by_width([p for p in strokes if classify_path(p) != FRAME])
        if by_w[1]:
            return by_w

    # Fail safe: if the page follows no convention we recognise, do not silently throw
    # the drawing away. Treat everything that is not the frame as geometry.
    if paths and len(geometry) < MIN_GEOMETRY_SHARE * len(paths):
        geometry = [p for p in paths if classify_path(p) != FRAME]

    return geometry, annotation
