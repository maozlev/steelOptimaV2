from pydantic import BaseModel, Field

CUTOUT_KINDS = ("hole", "slot", "notch", "freeform", "not_cutout")


class VlmVerdict(BaseModel):
    is_cutout: bool
    kind: str = Field(pattern="^(hole|slot|notch|freeform|not_cutout)$")
    confidence: float = Field(ge=0.0, le=1.0)


VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "is_cutout": {"type": "boolean"},
        "kind": {"type": "string", "enum": list(CUTOUT_KINDS)},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": ["is_cutout", "kind", "confidence"],
}

# schema is repeated in the prompt: this ollama build only enforces the
# `format` schema reliably in thinking mode, which is ~15x slower
CLASSIFY_CROP_PROMPT = (
    "This image is a cropped region from a steel manufacturing blueprint. "
    "The region of interest is the closed shape at the center of the crop "
    "(a candidate detected by a CV pipeline with low confidence). "
    "Decide whether it is a real manufacturing cutout that would be cut from "
    "the steel part, or drawing annotation. "
    "hole = circular cutout; slot = elongated cutout with straight or rounded "
    "ends; notch = cut into the part edge; freeform = any other genuine "
    "cutout contour; not_cutout = dimension text, label frame, title block "
    "cell, symbol, hatching or any other annotation. "
    'Respond ONLY with JSON: {"is_cutout": <bool>, '
    '"kind": "<hole|slot|notch|freeform|not_cutout>", "confidence": <0..1>}'
)
