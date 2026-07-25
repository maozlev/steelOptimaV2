"""L3 — the money metric: did we approve the right rows?

Cell accuracy is not the number that costs money. This is:

                    | should approve | should flag
    auto_approved   |       ok       |  MONEY LOST
    needs_review    |     a click    |      ok

The bottom-left cell must be empty. Everything here builds BOM PDFs whose right
answer is known by construction, corrupts specific cells, and asserts that a
corrupted row never auto-approves. No labelling needed, so this runs on every
commit — unlike the real-sheet fixtures, which need Maoz.

Generated text is clean vector type, so this proves NOTHING about OCR accuracy on
real CAD stroke glyphs (see tools/make_synthetic_tables.py's docstring). It
proves the decision layer: validation, the checksum boost, and the approve gate.
"""

import pytest

from tests.bom_factory import bom_row, by_item, run_bom

pytestmark = pytest.mark.slow  # OCR / CV / full job — see pyproject markers

CLEAN = [bom_row(801, 4, 1052), bom_row(802, 8, 743), bom_row(803, 2, 2077),
         bom_row(804, 6, 1357), bom_row(805, 3, 900)]


def test_clean_bom_auto_approves_every_row(client, wait_job, tmp_path):
    """The other half of the matrix: a table that is entirely correct must not
    drown the operator in review clicks."""
    result = run_bom(client, wait_job, tmp_path, "clean", CLEAN)
    assert len(result["rows"]) == len(CLEAN)
    flagged = [r for r in result["rows"] if r["status"] != "auto_approved"]
    assert not flagged, [(r["row_index"], r["flags"]) for r in flagged]
    assert result["table"]["validation"]["weight_total_matches"] is True


def test_broken_arithmetic_row_never_auto_approves(client, wait_job, tmp_path):
    """A digit wrong in the total-length column. The row's own checksum breaks;
    the WEIGHT column and the printed grand total still reconcile, so the
    table-level boost fires and confidence alone would have approved it. Only the
    row check stands between this and a wrong number in a bid."""
    rows = [dict(r) for r in CLEAN]
    rows[2]["total"] = str(int(rows[2]["total"]) + 1000)  # 4154 -> 5154
    result = run_bom(client, wait_job, tmp_path, "broken_len", rows)

    assert result["table"]["validation"]["weight_total_matches"] is True, (
        "the weight checksum must still pass — otherwise this test proves nothing"
    )
    bad = by_item(result["rows"]).get("803")
    assert bad is not None, "the corrupted row was not read back"
    assert bad["status"] == "needs_review", "WRONG VALUE AUTO-APPROVED"
    assert "qty_x_unit_length_mismatch" in bad["flags"]
    others = [r for r in result["rows"] if r is not bad]
    assert all(r["status"] == "auto_approved" for r in others), "collateral flagging"


def test_zero_and_fractional_qty_never_auto_approve(client, wait_job, tmp_path):
    rows = [dict(r) for r in CLEAN]
    rows[0].update(qty="0", total="0", weight="0.0")
    rows[1].update(qty="2.5")
    result = run_bom(client, wait_job, tmp_path, "bad_qty", rows)
    read = by_item(result["rows"])
    for item, expected_flag in (("801", "qty_not_positive"), ("802", "qty_not_integer")):
        row = read.get(item)
        assert row is not None, f"row {item} not read back"
        assert row["status"] == "needs_review", f"row {item}: WRONG VALUE AUTO-APPROVED"
        assert any(expected_flag in f or "mismatch" in f for f in row["flags"]), row["flags"]


def test_wrong_printed_grand_total_withholds_the_confidence_boost(
    client, wait_job, tmp_path
):
    """The table-level checksum is what lifts rows to 0.95 and lets them approve.
    When the printed total does NOT reconcile, that boost must not fire — a
    checksum that is wrong is not a checksum that passed."""
    result = run_bom(client, wait_job, tmp_path, "bad_total", CLEAN,
                     declared_total=9999.9)
    assert result["table"]["validation"]["weight_total_matches"] is False
    assert result["table"]["declared_total_weight_kg"] == 9999.9


def test_no_wrong_row_is_ever_auto_approved(client, wait_job, tmp_path):
    """The whole matrix in one assertion, over a table with several independent
    corruptions: recompute each row's arithmetic from what the pipeline itself
    read back, and require that anything inconsistent was flagged."""
    rows = [dict(r) for r in CLEAN]
    rows[0]["total"] = str(int(rows[0]["total"]) + 500)
    rows[2]["unit"] = str(int(rows[2]["unit"]) + 7)
    rows[4]["qty"] = "0"
    result = run_bom(client, wait_job, tmp_path, "multi", rows)

    money_lost = []
    for row in result["rows"]:
        if row["status"] != "auto_approved":
            continue
        qty, unit, total = row["qty"], row["unit_length_mm"], row["total_length_mm"]
        if qty is None or qty <= 0 or qty != int(qty):
            money_lost.append((row["row_index"], "bad qty", qty))
        elif unit and total and abs(qty * unit - total) > 0.005 * total:
            money_lost.append((row["row_index"], "arithmetic", (qty, unit, total)))
    assert money_lost == [], f"auto-approved rows that do not add up: {money_lost}"


@pytest.mark.parametrize("qty,unit", [(1, 500), (7, 12345), (12, 999)])
def test_single_row_boms_hold(client, wait_job, tmp_path, qty, unit):
    """Guard against the degenerate table: one data row, still classified and
    still validated (a one-row grid is where header/data confusion shows up)."""
    result = run_bom(client, wait_job, tmp_path, f"one_{qty}_{unit}",
                     [bom_row(900, qty, unit)])
    assert len(result["rows"]) == 1
    row = result["rows"][0]
    assert row["qty"] == qty and row["unit_length_mm"] == unit
    assert row["total_length_mm"] == qty * unit
    assert row["status"] == "auto_approved", row["flags"]
