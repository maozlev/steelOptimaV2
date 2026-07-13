import statistics

from app.extraction.ocr import annotated_ratio
from app.extraction.vector import Candidate

VECTOR_BASE = 0.6
RASTER_BASE = 0.45  # raster contours are noisier than exact vector paths
SHAPE_FIT_WEIGHT = 0.25
SIZE_PLAUSIBILITY_BONUS = 0.1
# keeps annotation-enclosed planar faces below the VLM escalation threshold
FREEFORM_PENALTY = 0.3
LOOP_BACKED_BONUS = 0.05
# a raster region that snapped to an ideal circle carries vector-grade
# certainty; without this the raster ceiling is 0.80 and clean bolt holes
# can never clear the 0.90 finalize/display threshold
RASTER_SNAP_BONUS = 0.12
RASTER_SNAP_MIN_FIT = 0.97
MAX_CONFIDENCE = 0.98

MIN_PARENT_RATIO = 1e-6
# Must track vector.MAX_CUTOUT_PARENT_RATIO. A gasket's bore fills 78% of its ring; with
# this left at 0.5 the bore was denied the size-plausibility bonus and landed a hair
# under the finalize threshold — extracted, then thrown away for being too big.
MAX_PARENT_RATIO = 0.90
TEXT_PENALTY_FACTOR = 0.4

# annotated dimension vs measured geometry, normalized by page drawing scale
DIMENSION_AGREEMENT_BONUS = 0.1
DIMENSION_CONFLICT_PENALTY = 0.15
DIMENSION_TOLERANCE = 0.05


def score_candidate(c: Candidate, dimension_agreement: bool | None = None) -> float:
    base = VECTOR_BASE if c.source == "vector" else RASTER_BASE
    score = base + SHAPE_FIT_WEIGHT * c.shape_fit

    ratio = c.polygon.area / c.parent_area if c.parent_area else 0.0
    if MIN_PARENT_RATIO <= ratio <= MAX_PARENT_RATIO:
        score += SIZE_PLAUSIBILITY_BONUS

    if c.kind == "freeform":
        score -= FREEFORM_PENALTY

    if c.from_loop:
        score += LOOP_BACKED_BONUS

    if (
        c.source == "raster_cv"
        and c.kind == "hole"
        and c.shape_fit >= RASTER_SNAP_MIN_FIT
    ):
        score += RASTER_SNAP_BONUS

    if dimension_agreement is True:
        score += DIMENSION_AGREEMENT_BONUS
    elif dimension_agreement is False:
        # measured vs annotated mismatch is a VLM escalation trigger
        score -= DIMENSION_CONFLICT_PENALTY

    # Text inside a TEXT-SIZED closed shape is an annotation box, not a cutout: a boxed
    # dimension, a title-block cell, a feature-control frame. Doc_HK3573's boxed "Ø605"
    # and "Ø642" callouts are perfect rectangles and would otherwise score 0.95 as slots.
    #
    # The size gate is what makes this safe, and it lives in vector.build_candidates and
    # ocr.annotate_candidates. Without it this penalty rejected any bore large enough to
    # hold its own dimension label — which killed ASH-071222's Ø290 bore, the only real
    # hole on that sheet. Kind is NOT the discriminator: an annotation box is a clean
    # rectangle, so exempting rectangles just lets every boxed callout through.
    if c.contains_text:
        score *= TEXT_PENALTY_FACTOR

    return round(min(max(score, 0.0), MAX_CONFIDENCE), 4)


def score_candidates(candidates: list[Candidate]) -> list[float]:
    """Page-level scoring: drawings are rarely 1:1, so dimension agreement is
    judged against the page's inferred scale (median annotated/measured ratio),
    not against absolute millimeters."""
    ratios = [r for c in candidates if (r := annotated_ratio(c)) is not None]
    page_scale = statistics.median(ratios) if len(ratios) >= 2 else 1.0

    scores = []
    for c in candidates:
        r = annotated_ratio(c)
        agreement = None
        if r is not None:
            agreement = abs(r - page_scale) <= DIMENSION_TOLERANCE * page_scale
        scores.append(score_candidate(c, dimension_agreement=agreement))
    return scores
