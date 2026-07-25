"""The classification gate: which grids get processed, which get dropped.

This is the highest-consequence decision in the tables pipeline and the one no
test covered. A grid that the gate calls "other" is persisted with status
"rejected" and — because aggregate.py counts only kind=="materials" toward
pending_tables/needs_review_rows — it never appears in the review queue and never
holds the project summary open. A wrongly-dropped BOM is therefore INVISIBLE:
the sheet contributes nothing and the summary still reports "nothing pending".

Pure unit tests on real OCR strings captured from the sample sheets. No PDF, no
OCR run, milliseconds.
"""

import pytest

from app.tables.cells import CellRead
from app.tables.classify import gate_decision


CONFIDENT = 0.95  # measured range for real printed English headers: 0.886-1.00


def reads(*texts: str | None, conf: float = CONFIDENT) -> list[CellRead]:
    """Header cells as the OCR hands them over: "" is an empty cell, None is
    inked-but-unread, anything else is a read at `conf`.

    The confidence matters as much as the text — see the Hebrew cases below."""
    out = []
    for t in texts:
        if t == "":
            out.append(CellRead(source="empty", ocr_conf=0.99, value=""))
        elif t is None:
            out.append(CellRead(source="ocr", ocr_conf=0.0, value=None))
        else:
            out.append(CellRead(raw_ocr=t, ocr_conf=conf, value=t, source="ocr"))
    return out


def numeric_row(n_cols: int) -> list[CellRead]:
    """A data row: digits, which the OCR reads confidently in any language."""
    return reads(*[str(100 + i) for i in range(n_cols)])


def blank(n: int) -> list[CellRead]:
    return reads(*[""] * n)


def candidates(header: list[CellRead], n_cols: int, data: bool = False):
    """A grid whose header is the top row. `data=True` makes the last grid row a
    numeric data row, which is what tells looks_tabular this is a real table."""
    last = numeric_row(n_cols) if data else blank(n_cols)
    return [
        ("top", 1, header),
        ("bottom", 1, last),
        ("top", 0, blank(n_cols)),
        ("bottom", 0, blank(n_cols)),
    ]


# --- the table we must always keep --------------------------------------------

def test_printed_english_headers_are_materials():
    header = reads("Item No", "Qty", "Item Description", "Unit Length", "Total Length")
    d = gate_decision(candidates(header, 5), 5)
    assert d.reason == "printed_headers"
    assert d.classification.kind == "materials"
    assert d.classification.column_roles[1] == "qty"
    assert not d.silently_dropped


def test_marker_words_without_clean_roles_still_get_processed():
    """Header the keyword map can't map, but the words say it's a material table.
    Must survive to the VLM, not be dropped."""
    header = reads("No.", "Pcs", "Section", "kg", "mm")
    d = gate_decision(candidates(header, 5), 5)
    assert d.markers is True
    assert d.reason == "marker_words"
    assert not d.silently_dropped


# --- the junk we must drop (precision) ----------------------------------------

def test_coordinate_list_is_dropped():
    """A readable header with no material words: correctly skipped, no VLM call."""
    header = reads("POINT", "NORTHING", "EASTING", "ELEV")
    d = gate_decision(candidates(header, 4), 4)
    assert d.readable is True and d.markers is False
    assert d.reason == "readable_no_markers"
    assert d.silently_dropped  # this one SHOULD be dropped


def test_grid_with_no_ink_at_all_is_dropped():
    d = gate_decision(candidates(blank(3), 3), 3)
    assert d.reason == "readable_no_markers"
    assert d.silently_dropped


# --- unreadable ink: the Hebrew case ------------------------------------------

# Verbatim OCR output for the header row of the 12x7 pile schedule on
# 833.1-01-20.pdf, WITH ITS MEASURED CONFIDENCES. RapidOCR cannot read Hebrew and
# those sheets carry no text layer (get_text() returns 0 characters on nine of
# ten), so the header comes back as confident-LOOKING Latin garbage. The text is
# indistinguishable from real words; the confidence is not.
HEBREW_HEADER_AS_OCR_READS_IT = [
    ("117V O9n I JU", 0.471), ("TIN", 0.318), ('NU n"On', 0.255),
    ("019'U 'on", 0.568), ("NTUNJ X", 0.512), ("i Y", 0.359), ("N DU", 0.754),
]

# A genuinely readable header with no material words — a table that SHOULD be
# dropped, and whose decision the gate reaches on solid evidence.
READABLE_NON_MATERIAL = ["POINT", "NORTHING", "EASTING", "ELEV"]


def hebrew_header() -> list[CellRead]:
    return [
        CellRead(raw_ocr=t, ocr_conf=c, value=t, source="ocr")
        for t, c in HEBREW_HEADER_AS_OCR_READS_IT
    ]


def test_hebrew_header_garbage_matches_no_marker_word():
    """Why keywords alone can never find these tables: there is nothing to match.
    Maoz asked for keyword-driven detection; on a sheet with no text layer and an
    OCR that cannot read the script, the keywords do not exist."""
    d = gate_decision(candidates(hebrew_header(), 7, data=True), 7, 12)
    assert d.markers is False


def test_low_confidence_garbage_is_recognised_as_unread():
    """The fix. `readable` used to mean "the OCR returned characters", which
    Hebrew stroke ink satisfies — so the escalation branch that exists FOR Hebrew
    was unreachable FROM Hebrew. It now means "returned characters it was
    confident about"."""
    d = gate_decision(candidates(hebrew_header(), 7, data=True), 7, 12)
    assert d.readable is False
    assert d.reason == "unreadable_ink"
    assert d.classification is None, "must reach the VLM, not a verdict"
    assert not d.silently_dropped


def test_confidence_is_what_separates_garbage_from_real_words():
    """The same seven strings read confidently WOULD be treated as real text.
    Text alone cannot tell them apart — this is the entire basis of the fix, so
    it is asserted directly rather than left implicit."""
    same_text_high_conf = [
        CellRead(raw_ocr=t, ocr_conf=CONFIDENT, value=t, source="ocr")
        for t, _c in HEBREW_HEADER_AS_OCR_READS_IT
    ]
    d = gate_decision(candidates(same_text_high_conf, 7, data=True), 7, 12)
    assert d.readable is True
    assert d.reason == "readable_no_markers"


def test_real_words_still_settle_it_without_the_vlm():
    """Precision must not pay for the fix: a header we genuinely read, with no
    material words in it, is still dropped on the spot. No VLM call."""
    d = gate_decision(candidates(reads(*READABLE_NON_MATERIAL), 4, data=True), 4, 9)
    assert d.readable is True
    assert d.reason == "readable_no_markers"
    assert d.silently_dropped


# --- unreadable AND not a table: the title blocks ------------------------------

def test_unreadable_title_block_does_not_earn_a_vlm_call():
    """There are a dozen unreadable grids on every Hebrew sheet and only one or
    two are tables. Structure is the only evidence left when the words are gone:
    a title block has no numeric data row, so it is dropped without a VLM call.
    Without this, the fix above would send every stray ruling to the model."""
    d = gate_decision(candidates(hebrew_header(), 7), 7, 20)
    assert d.readable is False
    assert d.tabular is False
    assert d.reason == "unreadable_not_tabular"
    assert d.silently_dropped


@pytest.mark.parametrize("n_rows,n_cols,data,tabular", [
    (12, 7, True, True),    # the pile schedule
    (3, 7, True, False),    # too few rows — a stray pair of rulings
    (12, 2, True, False),   # too few columns
    (12, 7, False, False),  # no numeric data row — a text block
])
def test_looks_tabular_needs_size_and_numbers(n_rows, n_cols, data, tabular):
    d = gate_decision(candidates(hebrew_header()[:n_cols], n_cols, data=data),
                      n_cols, n_rows)
    assert d.tabular is tabular


# --- properties ---------------------------------------------------------------

def test_a_dropped_grid_is_never_given_material_roles():
    """Defence in depth: if the gate drops a grid, it must not also hand out
    column roles that would let a later change resurrect it with wrong meaning."""
    d = gate_decision(candidates(reads("POINT", "NORTHING", "EASTING"), 3), 3)
    assert set(d.classification.column_roles) == {"other"}


def test_decision_is_pure_and_deterministic():
    c = candidates(reads("Item", "Qty", "Length"), 3)
    a, b = gate_decision(c, 3), gate_decision(c, 3)
    assert (a.reason, a.readable, a.markers) == (b.reason, b.readable, b.markers)
    assert a.classification.column_roles == b.classification.column_roles
