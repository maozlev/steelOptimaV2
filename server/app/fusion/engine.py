from app.vlm.prompts import VlmVerdict

# CV detections are already IoU-deduped and pass the hard geometric rules
# (closed, valid, min area, inside a parent) in build_candidates(); fusion's
# job here is combining the CV score with the VLM verdict for one region.
VLM_WEIGHT = 0.5
KIND_AGREEMENT_BONUS = 0.1
# a VLM rejection caps confidence: the more certain the rejection, the lower
REJECT_CEILING = 0.3
MAX_CONFIDENCE = 0.98


def fuse_verdict(
    cv_kind: str, cv_score: float, verdict: VlmVerdict
) -> tuple[str, float]:
    if not verdict.is_cutout or verdict.kind == "not_cutout":
        confidence = min(cv_score, REJECT_CEILING * (1.0 - verdict.confidence))
        return cv_kind, round(confidence, 4)

    fused = (1.0 - VLM_WEIGHT) * cv_score + VLM_WEIGHT * verdict.confidence
    if verdict.kind == cv_kind:
        fused += KIND_AGREEMENT_BONUS
    # the VLM sees the actual drawing, so it may name a shape the CV pipeline
    # could only call freeform
    kind = verdict.kind if cv_kind == "freeform" else cv_kind
    return kind, round(min(max(fused, 0.0), MAX_CONFIDENCE), 4)


# A vetoed cutout lands here: below the finalize threshold, so it will not be approved
# without a human looking at it, but nowhere near zero — it stays plainly visible in the
# review UI as "under review" and one click restores it.
VETO_CONFIDENCE = 0.50


def fuse_verification(cv_score: float, verdict: VlmVerdict) -> float:
    """Fuse a verdict on a candidate the CV pipeline is already CONFIDENT about.

    Asymmetric on purpose, in both directions.

    A confirmation must not MOVE the score at all. Averaging in the model's own
    confidence would demote a real hole the moment the model was merely 0.6 sure of
    something it got right: a confident CV detection plus a correct VLM agreement comes
    out at 0.89 and falls under the 0.90 finalize threshold. The model is here to object,
    not to grade.

    A rejection FLAGS, it does not delete. This model is not trustworthy enough to be
    given a delete key: asked about Doc_HK3573 it vetoed the real Ø605 bore with
    confidence 1.0. So a veto demotes the cutout to VETO_CONFIDENCE — below finalize, so
    nothing wrong is auto-approved, but visible and one click from being restored. Maoz's
    rule is that a false positive costs a click and a missed hole costs a part; a model
    that can silently erase a real hole gets that exactly backwards.
    """
    if not verdict.is_cutout or verdict.kind == "not_cutout":
        return round(min(cv_score, VETO_CONFIDENCE), 4)
    return cv_score
