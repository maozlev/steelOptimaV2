"""Separate part geometry from annotation, before any of it is polygonized.

The extractor used to polygonize every stroke on the page - part edges, leader lines,
arrowheads and glyph outlines alike - and then try to sort cutouts back out by shape
downstream. That cannot work: the `Ø` character in a label "Ø290 THRU" is drawn as
vector paths and is, geometrically, a circle. Shape alone will never tell it apart from
a small hole, and on ASH-071222 it was auto-approved at 0.98 while the real Ø290 bore
was rejected.

But CAD exports already say which ink is which, by stroke colour:

    part geometry      black   (0, 0, 0)
    dimensions/leaders grey    (0.5, 0.5, 0.5)   or olive (0.5, 0.5, 0)
    sheet frame        light   (0.75, 0.75, 0.75)

so we read it. Filtering to geometry ink removes glyphs, leader-line triangles and
arrowheads at source instead of fighting them with thresholds afterwards.

This is a heuristic about a convention, not a law, so it fails safe: if a page has no
dark ink at all (a mono export, an unusual palette), everything non-frame is treated as
geometry and we are no worse off than before.
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
