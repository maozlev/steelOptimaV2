from pathlib import Path

import fitz
import pytest
from shapely.geometry import Point, box

from app.extraction.ocr import OcrWord, annotate_candidates, parse_dimension
from app.extraction.scoring import score_candidate, score_candidates
from app.extraction.vector import Candidate, _classify

PDFS_DIR = Path(__file__).parent.parent.parent / "pdfs"

VLM_ESCALATION_THRESHOLD = 0.65


def _candidate(poly, **kw) -> Candidate:
    kind, fit, dims = _classify(poly)
    return Candidate(
        polygon=poly,
        kind=kind,
        shape_fit=fit,
        parent_area=kw.pop("parent_area", poly.area * 100),
        measured_dims=dims,
        **kw,
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Ø12.5", {"diameter_mm": 12.5}),
        ("⌀8", {"diameter_mm": 8.0}),
        ("ø 6,4", {"diameter_mm": 6.4}),
        ("40x20", {"length_mm": 40.0, "width_mm": 20.0}),
        ("20 X 40", {"length_mm": 40.0, "width_mm": 20.0}),
        ("HK3573", None),
        ("22,5", None),
    ],
)
def test_parse_dimension(text, expected):
    assert parse_dimension(text) == expected


def test_annotate_associates_nearest_diameter():
    hole = _candidate(Point(0, 0).buffer(10, quad_segs=32))
    words = [
        OcrWord(text="Ø12.5", bbox=(30, 30, 60, 40)),
        OcrWord(text="Ø99", bbox=(120, 120, 150, 130)),
    ]
    annotate_candidates([hole], words)
    assert hole.dimension_text == "Ø12.5"


def test_annotate_ignores_shape_misread_as_glyph():
    # a drilled hole OCRs as the letter "O" with a box bigger than the hole
    hole = _candidate(Point(0, 0).buffer(10, quad_segs=32))
    annotate_candidates([hole], [OcrWord(text="O", bbox=(-12, -12, 12, 12))])
    assert hole.contains_text is False


def test_annotate_flags_text_inside_annotation_box():
    frame = _candidate(box(0, 0, 100, 40))
    annotate_candidates([frame], [OcrWord(text="Ø642", bbox=(40, 15, 60, 25))])
    assert frame.contains_text is True


def test_raster_source_scores_below_vector():
    vector = _candidate(Point(0, 0).buffer(10, quad_segs=32))
    raster = _candidate(Point(0, 0).buffer(10, quad_segs=32), source="raster_cv")
    assert score_candidate(raster) < score_candidate(vector)


def test_dimension_agreement_uses_page_scale():
    # three Ø-annotated holes: two consistent with the 5:1 page scale, one off
    holes = [
        _candidate(Point(0, 0).buffer(10, quad_segs=32)) for _ in range(3)
    ]
    d = holes[0].measured_dims["diameter_mm"]
    holes[0].dimension_text = f"Ø{d * 5:.1f}"
    holes[1].dimension_text = f"Ø{d * 5:.1f}"
    holes[2].dimension_text = f"Ø{d * 8:.1f}"
    plain = _candidate(Point(0, 0).buffer(10, quad_segs=32))

    agree, agree2, conflict = score_candidates(holes)
    baseline = score_candidate(plain)
    assert agree == agree2 > baseline
    assert conflict < baseline


@pytest.fixture(scope="module")
def raster_pdf(tmp_path_factory):
    src = PDFS_DIR / "A (4).pdf"
    out = tmp_path_factory.mktemp("raster") / "raster_a4.pdf"
    with fitz.open(src) as doc:
        page = doc[0]
        pix = page.get_pixmap(dpi=150)
        synth = fitz.open()
        p = synth.new_page(width=page.rect.width, height=page.rect.height)
        p.insert_image(p.rect, pixmap=pix)
        synth.save(str(out))
        synth.close()
    return out


def test_raster_pipeline(client, wait_job, raster_pdf):
    r = client.post(
        "/api/documents",
        files={"file": (raster_pdf.name, raster_pdf.read_bytes(), "application/pdf")},
    )
    assert r.status_code == 201
    doc = r.json()
    assert doc["pages"][0]["kind"] == "raster"

    r = client.post(f"/api/documents/{doc['id']}/jobs")
    assert r.status_code == 202
    job = wait_job(client, r.json()["id"])
    assert job["status"] == "done"

    cutouts = client.get(f"/api/pages/{doc['pages'][0]['id']}/cutouts").json()
    confident_holes = [
        c
        for c in cutouts
        if c["kind"] == "hole" and c["confidence"] >= VLM_ESCALATION_THRESHOLD
    ]
    assert all(c["source"] == "raster_cv" for c in cutouts)
    # the perforated plate has 293 drilled holes (matches the vector pipeline)
    assert len(confident_holes) == 293
