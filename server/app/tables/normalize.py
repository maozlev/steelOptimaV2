"""Cell-text normalization: numbers, units, canonical material keys.

Pure functions — every OCR quirk fixed here gets a unit test, not a rerun.
"""

import re

# What the OCR actually gets wrong on stroke fonts is not digits but lookalikes:
# '×' for 'x', '[' / '［' / '丨' for 'L', 'm2' for 'm²'. Fix the glyphs, keep the value.
_HOMOGLYPHS = str.maketrans({"×": "x", "＊": "x", "Ｘ": "x", "ｘ": "x", "，": ",", "．": "."})
_L_LOOKALIKE_RE = re.compile(r"[\[［丨|](?=\s*\d)")


def fix_homoglyphs(text: str) -> str:
    return _L_LOOKALIKE_RE.sub("L", text.translate(_HOMOGLYPHS)).replace("m2", "m²")

# "3,814.4" / "3.814,4" / "400" / "+13.05" — OCR keeps digits well but mixes
# thousands separators; a lone comma between 3-digit groups is thousands
_NUM_RE = re.compile(r"^[+\-]?\d{1,3}(?:[,.]\d{3})*(?:[.,]\d+)?$|^[+\-]?\d+(?:[.,]\d+)?$")
_PLATE_RE = re.compile(r"^(\d+(?:[.,]\d+)?)\s*[xX×]\s*(\d+(?:[.,]\d+)?)$")
_PROFILE_RE = re.compile(
    r"([A-Za-z]{1,4})\s*\.?\s*(\d+(?:[.,]\d+)?(?:\s*[xX×]\s*\d+(?:[.,]\d+)?){1,3})"
)
# standard profile designators; the OCR merges words ("LegL160x160x15"), so the
# letters captured before the numbers are trimmed to the longest KNOWN suffix
_PROFILE_DESIGNATORS = {
    "L", "PL", "U", "C", "T", "I", "H", "W",
    "HEA", "HEB", "HEM", "IPE", "IPN", "UPN", "UNP",
    "RHS", "SHS", "CHS", "FL", "EA", "UA", "SQ", "RB",
}


def _trim_designator(letters: str) -> str:
    upper = letters.upper()
    for size in range(len(upper), 0, -1):
        if upper[-size:] in _PROFILE_DESIGNATORS:
            return upper[-size:]
    return upper
_THK_RE = re.compile(r"THK\s*\.?\s*(\d+(?:[.,]\d+)?)\s*mm", re.IGNORECASE)

UNIT_TO_MM = {"mm": 1.0, "cm": 10.0, "m": 1000.0}


def parse_number(raw: str | None) -> float | None:
    """A plain number or None — never a guess.

    '80x40' (a plate size), '0.6495 m²' (an area) and Hebrew text all return None;
    the caller decides what a non-numeric cell means for its column role.
    """
    if not raw:
        return None
    # the rec model sprinkles spaces into letter-spaced digits ("3 2.2"); a
    # numeric cell holds exactly one value, so internal whitespace is noise —
    # and a bad merge fails the row's own arithmetic anyway
    text = fix_homoglyphs(raw).replace(" ", "").replace("'", "").replace('"', "")
    if not _NUM_RE.match(text):
        return None
    # decide which separator is decimal: the LAST of . or , wins; the other is grouping
    last_dot, last_comma = text.rfind("."), text.rfind(",")
    if last_dot > last_comma:
        text = text.replace(",", "")
    else:
        text = text.replace(".", "").replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def to_mm(value: float, unit: str) -> float:
    return value * UNIT_TO_MM.get(unit, 1.0)


def parse_plate(raw: str | None) -> tuple[float, float] | None:
    """'450x174' -> (450.0, 174.0); anything else -> None."""
    if not raw:
        return None
    m = _PLATE_RE.match(fix_homoglyphs(raw).strip())
    if not m:
        return None
    a = parse_number(m.group(1))
    b = parse_number(m.group(2))
    if a is None or b is None:
        return None
    return (a, b)


def canonical_material_key(cells: dict[str, str | None]) -> str | None:
    """Stable grouping key for aggregation across documents.

    Prefers a steel-profile designation ('L 60x60x6' -> 'L60X60X6'), then a plate
    ('THK 14 mm' + '450x174' -> 'PLATE-14-450X174'), then numeric geometry
    (diameter/length pile schedules -> 'D60-L18'), then the raw description. The
    key survives an imperfect Hebrew description as long as the numbers read."""
    desc = fix_homoglyphs((cells.get("description") or "").strip())
    profile_text = fix_homoglyphs(cells.get("profile") or "") + " " + desc

    m = _PROFILE_RE.search(profile_text)
    if m:
        body = m.group(2).replace(" ", "").replace(",", ".").upper()
        return _trim_designator(m.group(1)) + body

    thk = _THK_RE.search(profile_text)
    plate = parse_plate(cells.get("unit_length")) or parse_plate(desc)
    if thk and plate:
        w, h = sorted(plate, reverse=True)
        return f"PLATE-{thk.group(1)}-{w:g}X{h:g}"

    dia = parse_number(cells.get("diameter"))
    length = parse_number(cells.get("unit_length"))
    if dia is not None and length is not None:
        return f"D{dia:g}-L{length:g}"
    if dia is not None:
        return f"D{dia:g}"

    return desc.upper() or None
