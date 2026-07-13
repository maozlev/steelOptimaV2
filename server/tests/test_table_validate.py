from app.tables.validate import row_status, validate_row, validate_table

ROLES = ["item_no", "qty", "description", "unit_length", "total_length", "total_weight"]


def _fields(**kw):
    base = {
        "qty": None,
        "unit_length_mm": None,
        "total_length_mm": None,
        "unit_weight_kg": None,
        "total_weight_kg": None,
    }
    base.update(kw)
    return base


def test_clean_row_passes():
    v = validate_row(
        _fields(qty=4, unit_length_mm=1052, total_length_mm=4208, total_weight_kg=22.8),
        ROLES,
    )
    assert v.ok
    assert v.checks_passed >= 3


def test_arithmetic_mismatch_flags():
    v = validate_row(
        _fields(qty=4, unit_length_mm=1052, total_length_mm=9999), ROLES
    )
    assert "qty_x_unit_length_mismatch" in v.flags


def test_rounding_tolerance():
    # printed totals are rounded: 8 x 743 = 5944 exactly, but allow 0.5%
    v = validate_row(_fields(qty=8, unit_length_mm=743, total_length_mm=5946), ROLES)
    assert v.ok


def test_qty_checks():
    assert "qty_missing" in validate_row(_fields(), ROLES).flags
    assert "qty_not_positive" in validate_row(_fields(qty=0), ROLES).flags
    assert "qty_not_integer" in validate_row(_fields(qty=2.5), ROLES).flags
    # a table with no qty column at all is not flagged for one
    assert validate_row(_fields(), ["description", "level"]).ok


def test_negative_values_flag():
    v = validate_row(_fields(qty=2, total_weight_kg=-5), ROLES)
    assert "total_weight_kg_not_positive" in v.flags


def test_plate_row_skips_length_arithmetic():
    # plates carry '450x174' in the length column -> parses to None -> no check
    v = validate_row(_fields(qty=8, total_weight_kg=43.0), ROLES)
    assert v.ok


def test_table_checksum():
    rows = [
        _fields(qty=2, total_weight_kg=651.6),
        _fields(qty=2, total_weight_kg=651.6),
    ]
    result = validate_table(rows, declared_total_weight_kg=1303.2)
    assert result["weight_total_matches"] is True
    result = validate_table(rows, declared_total_weight_kg=2000.0)
    assert result["weight_total_matches"] is False
    result = validate_table(rows, declared_total_weight_kg=None)
    assert result["weight_total_matches"] is None


def test_row_status_thresholds():
    good = validate_row(
        _fields(qty=4, unit_length_mm=100, total_length_mm=400), ROLES
    )
    assert row_status(good, confidence=0.95, approve_threshold=0.8) == "auto_approved"
    assert row_status(good, confidence=0.5, approve_threshold=0.8) == "needs_review"
    bad = validate_row(_fields(qty=None), ROLES)
    assert row_status(bad, confidence=0.99, approve_threshold=0.8) == "needs_review"
