import json
from pathlib import Path

import fitz
import pytest
from shapely.geometry import Point, Polygon, box

from app.extraction.ocr import OcrWord, annotate_candidates
from app.extraction.scoring import score_candidate
from app.extraction.vector import Candidate, _classify, extract_candidates

PDFS_DIR = Path(__file__).parent.parent.parent / "pdfs"

VLM_ESCALATION_THRESHOLD = 0.65


def _candidate(poly: Polygon, **kw) -> Candidate:
    kind, fit, dims = _classify(poly)
    return Candidate(
        polygon=poly,
        kind=kind,
        shape_fit=fit,
        parent_area=kw.pop("parent_area", poly.area * 100),
        measured_dims=dims,
        **kw,
    )


def test_classify_circle_as_hole():
    circle = Point(0, 0).buffer(10, quad_segs=32)
    kind, fit, dims = _classify(circle)
    assert kind == "hole"
    assert fit >= 0.90
    assert dims["diameter_mm"] == pytest.approx(20 * 25.4 / 72, abs=0.1)


def test_classify_rectangle_as_slot():
    rect = box(0, 0, 40, 10)
    kind, fit, dims = _classify(rect)
    assert kind == "slot"
    assert fit >= 0.95
    assert dims["length_mm"] == pytest.approx(40 * 25.4 / 72, abs=0.1)
    assert dims["width_mm"] == pytest.approx(10 * 25.4 / 72, abs=0.1)


def test_classify_l_shape_as_freeform():
    l_shape = Polygon([(0, 0), (30, 0), (30, 10), (10, 10), (10, 30), (0, 30)])
    kind, _, dims = _classify(l_shape)
    assert kind == "freeform"
    assert "bbox_w_mm" in dims


def test_hole_scores_above_escalation_threshold():
    c = _candidate(Point(0, 0).buffer(10, quad_segs=32), from_loop=True)
    assert score_candidate(c) > VLM_ESCALATION_THRESHOLD


def test_freeform_scores_below_escalation_threshold():
    l_shape = Polygon([(0, 0), (30, 0), (30, 10), (10, 10), (10, 30), (0, 30)])
    c = _candidate(l_shape)
    assert score_candidate(c) < VLM_ESCALATION_THRESHOLD


def test_text_penalty_pushes_annotation_box_below_threshold():
    """An annotation box is a freeform face that exists to hold text."""
    l_shape = Polygon([(0, 0), (30, 0), (30, 10), (10, 10), (10, 30), (0, 30)])
    c = _candidate(l_shape, contains_text=True)
    assert score_candidate(c) < VLM_ESCALATION_THRESHOLD


def test_text_inside_a_real_bore_is_not_penalised():
    """A dimension label sitting inside a large bore is ordinary CAD practice.

    ASH-071222's Ø290 bore has its own "Ø290 THRU" label inside it. Treating that as an
    annotation box multiplied the score by 0.4 and auto-rejected the only real hole on
    the sheet — while a Ø glyph elsewhere was auto-approved as a Ø3.1 hole.

    What protects it is the SIZE gate at candidate-build time: only a text-sized shape
    can be a text box. Forcing contains_text by hand would bypass the very mechanism
    under test, so this drives the real page.
    """
    page = fitz.open(PDFS_DIR / "ASH-071222-TW550-M10_BLANK.pdf")[0]
    cands = extract_candidates(page)
    words = [
        OcrWord(text=w[4], bbox=(w[0], w[1], w[2], w[3]))
        for w in page.get_text("words")
    ]
    annotate_candidates(cands, words)

    bore = next(c for c in cands if c.kind == "hole")
    assert not bore.contains_text, "a bore is not a box that exists to hold text"
    assert score_candidate(bore) > VLM_ESCALATION_THRESHOLD


def test_score_bounds():
    c = _candidate(Point(0, 0).buffer(10, quad_segs=32), from_loop=True)
    assert 0.0 < score_candidate(c) <= 0.98


# What the pipeline currently emits above the finalize threshold. This is a change
# detector, NOT a statement of correctness — for that see tests/fixtures/ground_truth.json
# and tools/eval_detection.py.
PIPELINE_CASES = {
    "A (3).pdf": {"hole": 128, "slot": 20},
    "A (4).pdf": {"hole": 293},
    # Doc_HK3573 is a gasket: 16 bolt holes + 1 central Ø605 bore = 17, confirmed by Maoz
    # against the drawing. Exactly 17 — no more. The title-block artifacts that used to
    # survive here (the "First Angle Projection" symbol's two circles, the ⊕□1 frame's
    # square) are gone: they sit outside the part, and a cutout is cut out of the PART.
    "Doc_HK3573_290626083217_00 (1).pdf": {"hole": 17},
}


@pytest.mark.parametrize("pdf_name", sorted(PIPELINE_CASES), ids=lambda n: n)
def test_extraction_pipeline(client, wait_job, pdf_name):
    pdf = PDFS_DIR / pdf_name
    r = client.post(
        "/api/documents",
        files={"file": (pdf.name, pdf.read_bytes(), "application/pdf")},
    )
    assert r.status_code == 201
    doc = r.json()

    r = client.post(f"/api/documents/{doc['id']}/jobs")
    assert r.status_code == 202
    assert r.json()["status"] == "queued"
    job = wait_job(client, r.json()["id"])
    assert job["status"] == "done"
    assert job["cutout_count"] > 0

    confident: dict[str, int] = {}
    for page in doc["pages"]:
        r = client.get(f"/api/pages/{page['id']}/cutouts")
        assert r.status_code == 200
        for c in r.json():
            assert c["source"] == "vector"
            assert 0.0 < c["confidence"] <= 0.98
            assert len(c["bbox"]) == 4
            if c["confidence"] >= VLM_ESCALATION_THRESHOLD:
                confident[c["kind"]] = confident.get(c["kind"], 0) + 1
                if c["kind"] == "hole":
                    dims = json.loads(c["measured_dims_json"])
                    assert dims["diameter_mm"] > 0

    assert confident == PIPELINE_CASES[pdf_name]


def test_overlay_render(client):
    r = client.get("/api/documents")
    pages = client.get(f"/api/documents/{r.json()[0]['id']}/pages").json()
    img = client.get(f"/api/pages/{pages[0]['id']}/render?overlay=true&min_conf=0.65")
    assert img.status_code == 200
    assert img.headers["content-type"] == "image/png"
