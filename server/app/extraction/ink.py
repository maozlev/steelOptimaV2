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


def split_ink(page: fitz.Page) -> tuple[list[dict], list[dict]]:
    """(geometry paths, annotation paths) for one page.

    Annotation paths are returned rather than discarded: dimension lines and their
    leaders are what `scale.py` measures the sheet scale from.

    Not every drawing follows the colour convention. Doc_HK3573 is wholly black and
    separates its layers by stroke WIDTH instead (378 thin paths against 81 thick), the
    other half of the ISO convention. Splitting on width was tried and measured: it did
    not recover a single extra cutout there, and it cost recall elsewhere. So it is not
    done. On such pages the later filters — the text penalty and the parent hierarchy —
    carry the load, and some annotation artifacts survive as false positives. That is the
    right trade: a false positive costs a click, a missed hole costs a part.
    """
    paths = page.get_drawings()
    geometry = [p for p in paths if classify_path(p) == GEOMETRY]
    annotation = [p for p in paths if classify_path(p) == ANNOTATION]

    # Fail safe: if the page follows no convention we recognise, do not silently throw
    # the drawing away. Treat everything that is not the frame as geometry.
    if paths and len(geometry) < MIN_GEOMETRY_SHARE * len(paths):
        geometry = [p for p in paths if classify_path(p) != FRAME]

    return geometry, annotation
