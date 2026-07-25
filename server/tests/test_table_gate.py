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


def reads(*texts: str | None) -> list[CellRead]:
    """Header cells as the OCR hands them over: "" is an empty cell, None is
    inked-but-unread (the Hebrew case), anything else is a read."""
    out = []
    for t in texts:
        if t == "":
            out.append(CellRead(source="empty", ocr_conf=0.99, value=""))
        elif t is None:
            out.append(CellRead(source="ocr", ocr_conf=0.0, value=None))
        else:
            out.append(CellRead(raw_ocr=t, ocr_conf=0.9, value=t, source="ocr"))
    return out


def blank(n: int) -> list[CellRead]:
    return reads(*[""] * n)


def candidates(header: list[CellRead], n_cols: int):
    """A grid whose header is the top row and whose other candidates are blank."""
    return [
        ("top", 1, header),
        ("bottom", 1, blank(n_cols)),
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


# --- the blind spot -----------------------------------------------------------

# Verbatim OCR output for the header row of the 12x7 pile schedule on
# 833.1-01-20.pdf. RapidOCR cannot read Hebrew and there is no text layer on
# those sheets (checked: get_text() returns 0 characters on nine of ten), so the
# header comes back as Latin garbage.
HEBREW_HEADER_AS_OCR_READS_IT = [
    "117V O9n I JU", "TIN", 'NU n"On', "019'U 'on", "NTUNJ X", "i Y", "N DU",
]

# A genuinely readable header with no material words — a table that SHOULD be
# dropped, and whose decision the gate reaches on solid evidence.
READABLE_NON_MATERIAL = ["POINT", "NORTHING", "EASTING", "ELEV"]


def test_hebrew_header_garbage_matches_no_marker_word():
    d = gate_decision(candidates(reads(*HEBREW_HEADER_AS_OCR_READS_IT), 7), 7)
    assert d.markers is False, "garbage cannot match a marker word — that is the trap"
    assert d.classification is None or d.classification.kind != "materials"


def test_garbage_and_real_text_are_indistinguishable_to_the_gate():
    """THE BLIND SPOT, pinned as a fact rather than asserted as a bug.

    `readable` means "the OCR returned characters", not "we understood them".
    Unreadable Hebrew ink and a genuine English coordinate header therefore reach
    the SAME decision by the SAME route — the gate cannot tell "there are no
    material words here" from "I could not read the words".

    On the sheets we have, both answers happen to be right: neither table is a
    BOM. The day a Hebrew sheet does carry one, this is the line that loses it,
    silently, with the VLM escalation branch that exists for exactly that case
    left untaken. Keyword matching cannot close this — there are no keywords to
    match. It needs an OCR model that reads Hebrew, or the VLM.
    """
    garbage = gate_decision(candidates(reads(*HEBREW_HEADER_AS_OCR_READS_IT), 7), 7)
    real = gate_decision(candidates(reads(*READABLE_NON_MATERIAL), 4), 4)
    assert garbage.readable == real.readable is True
    assert garbage.reason == real.reason == "readable_no_markers"


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
