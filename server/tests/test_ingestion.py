from pathlib import Path

import pytest

PDFS_DIR = Path(__file__).parent.parent.parent / "pdfs"


@pytest.mark.parametrize("pdf", sorted(PDFS_DIR.glob("*.pdf")), ids=lambda p: p.name)
def test_ingest_and_render(client, pdf):
    r = client.post(
        "/api/documents",
        files={"file": (pdf.name, pdf.read_bytes(), "application/pdf")},
    )
    assert r.status_code == 201
    doc = r.json()
    assert doc["page_count"] == len(doc["pages"]) > 0

    for page in doc["pages"]:
        assert page["kind"] in ("vector", "raster", "mixed")
        img = client.get(f"/api/pages/{page['id']}/render")
        assert img.status_code == 200
        assert img.headers["content-type"] == "image/png"


def test_duplicate_rejected(client):
    pdf = next(iter(sorted(PDFS_DIR.glob("*.pdf"))))
    r = client.post(
        "/api/documents",
        files={"file": (pdf.name, pdf.read_bytes(), "application/pdf")},
    )
    assert r.status_code == 409


def test_non_pdf_rejected(client):
    r = client.post("/api/documents", files={"file": ("x.txt", b"hi", "text/plain")})
    assert r.status_code == 400
    assert "PDF, JPEG, or PNG" in r.json()["detail"]


def _image_bytes(fmt: str) -> bytes:
    import fitz

    pdf = next(iter(sorted(PDFS_DIR.glob("*.pdf"))))
    with fitz.open(pdf) as d:
        pix = d[0].get_pixmap(dpi=36)
        return pix.tobytes(fmt)


@pytest.mark.parametrize("ext,fmt,mime", [
    ("png", "png", "image/png"),
    ("jpg", "jpeg", "image/jpeg"),
])
def test_image_upload(client, ext, fmt, mime):
    r = client.post(
        "/api/documents",
        files={"file": (f"blueprint.{ext}", _image_bytes(fmt), mime)},
    )
    assert r.status_code == 201, r.text
    doc = r.json()
    assert doc["page_count"] == 1
    assert doc["status"] == "pending"
    page = doc["pages"][0]
    assert page["kind"] == "raster"
    img = client.get(f"/api/pages/{page['id']}/render")
    assert img.status_code == 200
    assert img.headers["content-type"] == "image/png"


def _synthetic_pdf(label: str) -> bytes:
    import fitz

    with fitz.open() as d:
        page = d.new_page(width=500, height=400)
        # a circle in the middle (extraction candidate) + text near the edge
        page.draw_circle(fitz.Point(250, 200), 20)
        page.insert_text(fitz.Point(20, 20), label)
        return d.tobytes()


def _upload_synthetic(client, label: str) -> dict:
    r = client.post(
        "/api/documents",
        files={"file": (f"{label}.pdf", _synthetic_pdf(label), "application/pdf")},
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_crop_document(client):
    doc = _upload_synthetic(client, "crop_happy")
    orig_w = doc["pages"][0]["width_pt"]
    orig_h = doc["pages"][0]["height_pt"]
    r = client.post(
        f"/api/documents/{doc['id']}/crop",
        json={"x_min": 0.1, "y_min": 0.1, "x_max": 0.9, "y_max": 0.9},
    )
    assert r.status_code == 200, r.text
    page = r.json()["pages"][0]
    assert abs(page["width_pt"] - 0.8 * orig_w) < 1.0
    assert abs(page["height_pt"] - 0.8 * orig_h) < 1.0
    img = client.get(f"/api/pages/{page['id']}/render")
    assert img.status_code == 200


def test_crop_rotated_page(client):
    import fitz

    with fitz.open() as d:
        page = d.new_page(width=500, height=400)
        page.draw_circle(fitz.Point(250, 200), 20)
        page.insert_text(fitz.Point(20, 20), "crop_rotated")
        page.set_rotation(90)
        pdf_bytes = d.tobytes()
    r = client.post(
        "/api/documents",
        files={"file": ("crop_rotated.pdf", pdf_bytes, "application/pdf")},
    )
    assert r.status_code == 201, r.text
    doc = r.json()
    # displayed (rotated) size is 400x500
    assert abs(doc["pages"][0]["width_pt"] - 400) < 1.0
    assert abs(doc["pages"][0]["height_pt"] - 500) < 1.0
    r = client.post(
        f"/api/documents/{doc['id']}/crop",
        json={"x_min": 0.1, "y_min": 0.1, "x_max": 0.9, "y_max": 0.9},
    )
    assert r.status_code == 200, r.text
    page = r.json()["pages"][0]
    assert abs(page["width_pt"] - 0.8 * 400) < 1.0
    assert abs(page["height_pt"] - 0.8 * 500) < 1.0
    img = client.get(f"/api/pages/{page['id']}/render")
    assert img.status_code == 200


def test_crop_invalid_coords_422(client):
    doc = _upload_synthetic(client, "crop_invalid")
    for body in (
        {"x_min": 0.9, "y_min": 0.1, "x_max": 0.1, "y_max": 0.9},
        {"x_min": -0.1, "y_min": 0.0, "x_max": 1.0, "y_max": 1.0},
        {"x_min": 0.0, "y_min": 0.0, "x_max": 1.0, "y_max": 1.1},
    ):
        assert client.post(f"/api/documents/{doc['id']}/crop", json=body).status_code == 422


def test_crop_full_area_noop(client):
    doc = _upload_synthetic(client, "crop_noop")
    orig_w = doc["pages"][0]["width_pt"]
    r = client.post(
        f"/api/documents/{doc['id']}/crop",
        json={"x_min": 0.0, "y_min": 0.0, "x_max": 1.0, "y_max": 1.0},
    )
    assert r.status_code == 200
    assert r.json()["pages"][0]["width_pt"] == orig_w


def test_crop_after_job_409(client, wait_job):
    doc = _upload_synthetic(client, "crop_after_job")
    job = client.post(f"/api/documents/{doc['id']}/jobs").json()
    wait_job(client, job["id"])
    r = client.post(
        f"/api/documents/{doc['id']}/crop",
        json={"x_min": 0.1, "y_min": 0.1, "x_max": 0.9, "y_max": 0.9},
    )
    assert r.status_code == 409


def test_extraction_after_crop_within_bounds(client, wait_job):
    doc = _upload_synthetic(client, "crop_extract")
    cropped = client.post(
        f"/api/documents/{doc['id']}/crop",
        json={"x_min": 0.1, "y_min": 0.1, "x_max": 0.9, "y_max": 0.9},
    ).json()
    page = cropped["pages"][0]
    job = client.post(f"/api/documents/{doc['id']}/jobs").json()
    assert wait_job(client, job["id"])["status"] == "done"
    import json as _json

    cutouts = client.get(f"/api/pages/{page['id']}/cutouts").json()
    for c in cutouts:
        x0, y0, x1, y1 = (
            c["bbox"] if isinstance(c["bbox"], list) else _json.loads(c["bbox"])
        )
        assert x1 > 0 and y1 > 0
        assert x0 < page["width_pt"] and y0 < page["height_pt"]
