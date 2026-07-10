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
