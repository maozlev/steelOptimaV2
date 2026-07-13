"""Deterministic row/table validation.

The accuracy story of this pipeline is not "the models are good" — it is that a
materials table carries its own checksums. qty × unit length must equal the printed
total length; the weight column must sum to the printed grand total. A row that
passes every check auto-approves; anything else is flagged for a human. A wrong
value that gets FLAGGED costs a click; a wrong value that slips through unflagged
costs money — so every check errs toward flagging.
"""

from dataclasses import dataclass, field

REL_TOL = 0.005  # printed totals are rounded to 0.1 — half a percent covers that
QTY_INT_TOL = 1e-6


@dataclass
class RowValidation:
    flags: list[str] = field(default_factory=list)
    checks_passed: int = 0

    @property
    def ok(self) -> bool:
        return not self.flags


def _close(a: float, b: float, tol: float = REL_TOL) -> bool:
    return abs(a - b) <= tol * max(abs(a), abs(b), 1.0)


def validate_row(fields: dict[str, float | None], roles: list[str]) -> RowValidation:
    """fields: normalized numbers (qty, unit_length_mm, total_length_mm,
    unit_weight_kg, total_weight_kg), None where the cell didn't parse.
    roles: the table's column roles — a check only applies when its columns exist."""
    v = RowValidation()
    qty = fields.get("qty")
    unit_len = fields.get("unit_length_mm")
    total_len = fields.get("total_length_mm")
    unit_w = fields.get("unit_weight_kg")
    total_w = fields.get("total_weight_kg")

    if "qty" in roles:
        if qty is None:
            v.flags.append("qty_missing")
        elif qty <= 0:
            v.flags.append("qty_not_positive")
        elif abs(qty - round(qty)) > QTY_INT_TOL:
            v.flags.append("qty_not_integer")
        else:
            v.checks_passed += 1

    for name, value in (
        ("unit_length_mm", unit_len),
        ("total_length_mm", total_len),
        ("unit_weight_kg", unit_w),
        ("total_weight_kg", total_w),
    ):
        if value is not None and value <= 0:
            v.flags.append(f"{name}_not_positive")

    # the row's own arithmetic — the strongest signal available
    if qty and unit_len and total_len:
        if _close(qty * unit_len, total_len):
            v.checks_passed += 2
        else:
            v.flags.append("qty_x_unit_length_mismatch")
    if qty and unit_w and total_w:
        if _close(qty * unit_w, total_w):
            v.checks_passed += 2
        else:
            v.flags.append("qty_x_unit_weight_mismatch")

    return v


def validate_table(
    rows: list[dict[str, float | None]],
    declared_total_weight_kg: float | None,
) -> dict:
    """Table-level checksum: the weight column against the printed grand total.

    When it reconciles, every row that contributed gets its confidence boosted —
    the printed total is a checksum over the whole column, misreads do not cancel."""
    weights = [r.get("total_weight_kg") for r in rows]
    summed = sum(w for w in weights if w is not None)
    result: dict = {
        "declared_total_weight_kg": declared_total_weight_kg,
        "summed_total_weight_kg": round(summed, 3) if summed else None,
        "weight_total_matches": None,
    }
    if declared_total_weight_kg and summed:
        # the printed grand total is rounded to one decimal; allow one unit of
        # rounding per row on top of the relative tolerance
        tol = max(REL_TOL * declared_total_weight_kg, 0.05 * len(rows))
        result["weight_total_matches"] = (
            abs(summed - declared_total_weight_kg) <= tol
        )
    return result


def row_status(
    validation: RowValidation, confidence: float, approve_threshold: float
) -> str:
    if validation.ok and confidence >= approve_threshold:
        return "auto_approved"
    return "needs_review"
