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

# The CV pipeline is CONFIDENT about these — and that is exactly the problem. A GD&T
# feature-control frame is a circle and a square; a boxed dimension callout is a perfect
# rectangle. Geometry cannot tell them from a hole and a slot, because geometrically they
# ARE a hole and a slot. Only a reader who understands what a drawing MEANS can separate
# them, which is the one thing a vision model is genuinely better at than any rule.
#
# The crop carries generous surroundings on purpose: whether a circle is a hole depends
# entirely on whether it sits in the metal or in the margin.
VERIFY_CROP_PROMPT = (
    "This is a region of a steel fabrication drawing. A CV pipeline believes the shape "
    "marked by the RED outline is a manufacturing cutout — a hole or slot that will be "
    "physically cut out of the steel plate. Your job is to catch it when it is wrong.\n\n"
    "Answer one question: would a machinist actually CUT this shape out of the metal?\n\n"
    "It IS a cutout if it is a hole, slot, notch or opening drawn on the part itself — "
    "part of the physical geometry of the steel.\n\n"
    "It is NOT a cutout (kind = not_cutout) if it is any of the following, no matter how "
    "circular or rectangular it looks:\n"
    "- a GD&T / feature-control frame (a boxed row of symbols such as ⊕, ⌀, ▱, often with "
    "a datum letter or number)\n"
    "- a datum target, datum symbol or the circled letter/number beside one\n"
    "- a boxed or framed dimension callout (a number in a rectangle)\n"
    "- a title-block cell, a revision balloon, a section or detail marker\n"
    "- a leader line, arrowhead, centre-line, hatching, or a printed character\n"
    "- anything drawn in the margin or beside the part rather than on it\n\n"
    "A drawing SYMBOL is drawn on the paper. A CUTOUT is drawn on the metal. If the shape "
    "sits outside the body of the part, it is not a cutout.\n\n"
    "Be decisive. If it is a symbol or annotation, say so.\n"
    'Respond ONLY with JSON: {"is_cutout": <bool>, '
    '"kind": "<hole|slot|notch|freeform|not_cutout>", "confidence": <0..1>}'
)
