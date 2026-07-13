from pydantic import BaseModel, Field

CUTOUT_KINDS = ("hole", "slot", "notch", "freeform", "not_cutout")

TABLE_KINDS = ("materials", "coordinates", "other")
COLUMN_ROLES = (
    "item_no",
    "qty",
    "description",
    "profile",
    "diameter",
    "unit_length",
    "total_length",
    "unit_weight",
    "total_weight",
    "level",
    "other",
)


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

class TableVerdict(BaseModel):
    kind: str = Field(pattern="^(materials|coordinates|other)$")
    title: str = ""
    column_roles: list[str]
    header_rows: int = Field(ge=0, le=4)
    header_position: str = Field(default="top", pattern="^(top|bottom)$")
    length_unit: str = Field(default="mm", pattern="^(mm|cm|m)$")
    confidence: float = Field(ge=0.0, le=1.0)


TABLE_VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": list(TABLE_KINDS)},
        "title": {"type": "string"},
        "column_roles": {
            "type": "array",
            "items": {"type": "string", "enum": list(COLUMN_ROLES)},
        },
        "header_rows": {"type": "integer", "minimum": 0, "maximum": 4},
        "header_position": {"type": "string", "enum": ["top", "bottom"]},
        "length_unit": {"type": "string", "enum": ["mm", "cm", "m"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": [
        "kind",
        "title",
        "column_roles",
        "header_rows",
        "header_position",
        "length_unit",
        "confidence",
    ],
}


def classify_table_prompt(n_cols: int) -> str:
    return (
        "This image is a table cropped from an engineering drawing (it may be in "
        "Hebrew, read right-to-left, or in English). Classify it.\n\n"
        "kind = 'materials' if the table lists physical items to build or order — "
        "it has quantities together with dimensions, lengths, diameters or weights "
        "(a bill of materials, steel profile list, pile/column schedule, concrete "
        "component list). kind = 'coordinates' if its main content is X/Y survey "
        "coordinates. kind = 'other' for revision histories, sign-off/distribution "
        "matrices, title blocks, legends, and anything else.\n\n"
        f"The grid has exactly {n_cols} columns. Report column_roles as an array of "
        f"exactly {n_cols} entries, LEFT to RIGHT in the image: item_no (row number), "
        "qty (how many), description (item name/text), profile (steel profile like "
        "L60x60x6), diameter, unit_length (length of one piece), total_length, "
        "unit_weight, total_weight, level (elevation), other.\n\n"
        "header_rows = how many grid rows are column headings; header_position = "
        "whether those headings are at the top or the BOTTOM of the table (some CAD "
        "tables build upward). length_unit = the unit its length/diameter columns "
        "use. title = the table's caption if visible, else ''."
    )


ROW_VALUES_SCHEMA = {
    "type": "object",
    "properties": {
        "values": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["values"],
}


def transcribe_row_prompt(n_cols: int) -> str:
    return (
        "This image is ONE row cut from an engineering-drawing table. It contains "
        f"exactly {n_cols} cells separated by vertical lines. Transcribe each cell "
        "EXACTLY as printed, LEFT to RIGHT in the image, into `values` (exactly "
        f"{n_cols} strings). Keep digits, decimal points, x separators and units "
        "verbatim; use '' for an empty cell. Hebrew text must be transcribed as "
        "written. Do not compute, correct or reformat anything."
    )


def transcribe_column_prompt(n_rows: int) -> str:
    return (
        "This image is ONE column cut from an engineering-drawing table. It contains "
        f"exactly {n_rows} cells separated by horizontal lines. Transcribe each cell "
        "EXACTLY as printed, TOP to BOTTOM, into `values` (exactly "
        f"{n_rows} strings). The text may be Hebrew (read each cell right-to-left) "
        "or English. Use '' for an empty cell. Do not compute, correct or reformat "
        "anything."
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
