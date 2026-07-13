from app.tables.normalize import (
    canonical_material_key,
    fix_homoglyphs,
    parse_number,
    parse_plate,
    to_mm,
)


def test_parse_number_plain():
    assert parse_number("400") == 400.0
    assert parse_number("0.45") == 0.45
    assert parse_number("+13.05") == 13.05
    assert parse_number("-2,5") == -2.5


def test_parse_number_thousands():
    assert parse_number("3,814.4") == 3814.4
    assert parse_number("18 000") == 18000.0
    assert parse_number("1.234,5") == 1234.5


def test_parse_number_rejects_non_numbers():
    assert parse_number("80x40") is None
    assert parse_number("0.0256 m²") is None
    assert parse_number("") is None
    assert parse_number(None) is None
    assert parse_number("כלונסאות") is None


def test_to_mm():
    assert to_mm(20, "m") == 20000.0
    assert to_mm(80, "cm") == 800.0
    assert to_mm(9000, "mm") == 9000.0


def test_parse_plate():
    assert parse_plate("450x174") == (450.0, 174.0)
    assert parse_plate("80×40") == (80.0, 40.0)  # OCR homoglyph ×
    assert parse_plate("9000") is None


def test_homoglyphs():
    assert fix_homoglyphs("L60x60×6") == "L60x60x6"
    assert fix_homoglyphs("Diagonal [60x60x6") == "Diagonal L60x60x6"
    assert fix_homoglyphs("Diagona1丨90x90x9") == "Diagona1L90x90x9"
    assert fix_homoglyphs("0.6495 m2") == "0.6495 m²"


def test_material_key_profile():
    assert (
        canonical_material_key({"description": "Horizontal L60x60x6"}) == "L60X60X6"
    )
    assert (
        canonical_material_key({"description": "Diagonal [90x90x9"}) == "L90X90X9"
    )
    assert (
        canonical_material_key({"description": "Leg L160x160x15"}) == "L160X160X15"
    )


def test_material_key_plate():
    key = canonical_material_key(
        {"description": "Connection Plate THK 14 mm", "unit_length": "450x174"}
    )
    assert key == "PLATE-14-450X174"


def test_material_key_pile_from_numbers():
    # Hebrew pile schedule: description unreadable, numbers carry the identity
    key = canonical_material_key(
        {"description": "", "diameter": "80", "unit_length": "20"}
    )
    assert key == "D80-L20"


def test_material_key_fallback_description():
    assert canonical_material_key({"description": "בטון הפלסה"}) == "בטון הפלסה"
    assert canonical_material_key({"description": ""}) is None
