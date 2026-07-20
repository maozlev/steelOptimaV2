from pathlib import Path

from app.planning.analyze import analyze_pdf

# same source PDFs the table-review tests use: repo-root/tables
TABLES_DIR = Path(__file__).parent.parent.parent / "tables"


def test_analyze_ncd_pdf_proposes_material_items():
    data = (TABLES_DIR / "NCD5168[_EN](5).pdf").read_bytes()
    out = analyze_pdf(data)
    assert out["source"] == "table_ocr"
    keys = {i["material_key"] for i in out["items"]}
    # the BOM's headline materials must surface as proposals
    assert "L160X160X15" in keys
    assert any(k.startswith("PLATE-") for k in keys)
    # every item is actionable: a key and a positive qty
    for item in out["items"]:
        assert item["material_key"]
        assert item["qty"] > 0
        assert item["source"] == "table_ocr"
    # bar rows carry a length; plate rows carry dims instead
    legs = next(i for i in out["items"] if i["material_key"] == "L160X160X15")
    assert legs["unit_length_mm"] and legs["unit_length_mm"] > 0
    plate = next(i for i in out["items"] if i["material_key"].startswith("PLATE-"))
    assert plate["w_mm"] and plate["h_mm"] and plate["thk_mm"]


def test_analyze_pdf_without_tables_warns():
    import fitz

    doc = fitz.open()
    doc.new_page()
    out = analyze_pdf(doc.tobytes())
    assert out["items"] == []
    assert out["warnings"]
