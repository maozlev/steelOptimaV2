"""Header -> column roles, and where the header is. Pure functions, milliseconds.

These decide what every number in a row MEANS. A unit_length column read as
total_length makes qty x unit == total pass against the wrong pair, so the row
auto-approves with a length that is off by a factor of qty — the validation layer
cannot catch it, because from its point of view the arithmetic holds.

Kept free of OCR on purpose: this is the layer that should be cheap to test
exhaustively, so new header wording can be added in seconds.
"""

import pytest

from app.tables.classify import (
    TableClassification,
    classification_to_json,
    classify_heuristic,
    data_row_indices,
    has_material_markers,
)
from app.tables.grid import TableGrid


def one(texts: list[str], position: str = "top", header_rows: int = 1):
    return classify_heuristic([(position, header_rows, texts)])


# --- role mapping -------------------------------------------------------------

@pytest.mark.parametrize("text,role", [
    ("Qty", "qty"),
    ("QTY.", "qty"),
    ("Quantity", "qty"),
    ("Item No", "item_no"),
    ("Item Description", "description"),
    ("Description", "description"),
    ("Unit Length [mm]", "unit_length"),
    ("Total Length [mm]", "total_length"),
    ("Unit Weight (kg)", "unit_weight"),
    ("Total Weight [kg]", "total_weight"),
    ("Profile", "profile"),
    ("Section", "profile"),
    ("Diameter", "diameter"),
    ("Dia. mm", "diameter"),
    ("Level", "level"),
    ("Notes", "other"),
    ("Colour", "other"),
    ("", "other"),
])
def test_single_header_word_maps_to_its_role(text, role):
    assert one([text]).column_roles == [role]


def test_specific_phrases_beat_generic_ones():
    """'Total Length' must not be swallowed by the bare 'length' rule, and
    'Total Weight' must not become 'unit weight'. Ordering in _HEADER_KEYWORDS is
    load-bearing; this is what stops a reorder from silently swapping columns."""
    roles = one(["Length", "Total Length", "Unit Length",
                 "Weight", "Total Weight", "Unit Weight"]).column_roles
    assert roles == ["unit_length", "total_length", "unit_length",
                     "total_weight", "total_weight", "unit_weight"]


def test_case_and_spacing_do_not_matter():
    assert one(["  tOtAl WeIgHt  "]).column_roles == ["total_weight"]


def test_materials_needs_both_a_count_and_a_dimension():
    """qty alone is a parts list; a dimension alone is a schedule. Only both
    together make a table the pipeline will read rows out of."""
    assert one(["Qty", "Notes"]).kind == "unknown"
    assert one(["Profile", "Notes"]).kind == "unknown"
    assert one(["Qty", "Profile"]).kind == "materials"
    assert one(["Qty", "Unit Length"]).kind == "materials"
    assert one(["Qty", "Diameter"]).kind == "materials"


def test_confidence_rises_with_recognised_columns():
    assert one(["Qty", "Profile"]).confidence == 0.2
    assert one(["Item No", "Qty", "Profile"]).confidence == 0.5


def test_best_candidate_wins_across_positions():
    """The NCD case: the top grid row is data, the real header is the strip
    BELOW the grid. The candidate that recognises more columns must win, and
    must carry its own position/header_rows with it."""
    cls = classify_heuristic([
        ("top", 1, ["830", "8", "Reinforcement", "80x40"]),
        ("bottom", 0, ["Item No", "Qty", "Description", "Unit Length"]),
    ])
    assert cls.kind == "materials"
    assert cls.header_position == "bottom"
    assert cls.header_rows == 0
    assert cls.column_roles == ["item_no", "qty", "description", "unit_length"]


def test_no_candidates_still_yields_a_role_per_column():
    """Downstream zips roles against cells; a short list would silently drop the
    tail columns rather than raise."""
    cls = classify_heuristic([("top", 1, ["", "", "", "", ""])])
    assert cls.column_roles == ["other"] * 5


# --- marker words -------------------------------------------------------------

@pytest.mark.parametrize("texts", [
    ["Total Weight"], ["kg"], ["Unit Length mm"], ["QTY"], ["PCS"],
    ["Profile"], ["Size"], ["משקל"], ["אורך"], ["קוטר"], ["כמות"],
])
def test_material_markers_hit(texts):
    assert has_material_markers(texts) is True


@pytest.mark.parametrize("texts", [
    ["POINT", "NORTHING", "EASTING"],
    ["REV", "DATE", "DRAWN", "CHECKED"],
    [""], [], [None],
])
def test_material_markers_miss(texts):
    assert has_material_markers(texts) is False


# --- which rows hold data -----------------------------------------------------

def grid(n_rows: int) -> TableGrid:
    return TableGrid(
        bbox=(0, 0, 10, float(n_rows)),
        col_edges=[0, 10],
        row_edges=[float(i) for i in range(n_rows + 1)],
    )


def test_header_at_top_is_skipped():
    cls = TableClassification(header_rows=1, header_position="top")
    assert data_row_indices(grid(5), cls) == [1, 2, 3, 4]


def test_header_at_bottom_is_skipped():
    cls = TableClassification(header_rows=1, header_position="bottom")
    assert data_row_indices(grid(5), cls) == [0, 1, 2, 3]


def test_header_outside_the_grid_keeps_every_row():
    cls = TableClassification(header_rows=0, header_position="bottom")
    assert data_row_indices(grid(5), cls) == [0, 1, 2, 3, 4]


def test_header_claim_can_never_consume_the_whole_table():
    """A model claiming 9 header rows on a 3-row grid must not leave zero data
    rows — the table would silently read as empty."""
    cls = TableClassification(header_rows=9, header_position="top")
    rows = data_row_indices(grid(3), cls)
    assert rows == [2]


def test_roles_serialise_with_their_column_index():
    cls = TableClassification(column_roles=["qty", "other", "total_weight"])
    assert classification_to_json(cls) == (
        '[{"index": 0, "role": "qty"}, {"index": 1, "role": "other"}, '
        '{"index": 2, "role": "total_weight"}]'
    )
