from pathlib import Path

import pytest

from app.fusion.engine import fuse_verdict
from app.vlm.client import OllamaVlmClient, VlmResult, _strip_fences
from app.vlm.escalation import select_for_escalation
from app.vlm.prompts import VlmVerdict

PDFS_DIR = Path(__file__).parent.parent.parent / "pdfs"
DOC3 = "Doc_HK3573_290626083217_00 (1).pdf"


# --- fuse_verdict ---


def test_fuse_rejection_caps_confidence():
    verdict = VlmVerdict(is_cutout=False, kind="not_cutout", confidence=1.0)
    kind, conf = fuse_verdict("hole", 0.6, verdict)
    assert kind == "hole"
    assert conf == 0.0


def test_fuse_uncertain_rejection_leaves_some_confidence():
    verdict = VlmVerdict(is_cutout=False, kind="not_cutout", confidence=0.5)
    _, conf = fuse_verdict("hole", 0.6, verdict)
    assert conf == pytest.approx(0.15)


def test_fuse_rejection_never_raises_confidence():
    verdict = VlmVerdict(is_cutout=False, kind="not_cutout", confidence=0.0)
    _, conf = fuse_verdict("hole", 0.1, verdict)
    assert conf == pytest.approx(0.1)


def test_fuse_agreement_bonus():
    verdict = VlmVerdict(is_cutout=True, kind="hole", confidence=0.9)
    kind, conf = fuse_verdict("hole", 0.5, verdict)
    assert kind == "hole"
    assert conf == pytest.approx(0.5 * 0.5 + 0.5 * 0.9 + 0.1)


def test_fuse_disagreement_keeps_cv_kind():
    verdict = VlmVerdict(is_cutout=True, kind="slot", confidence=0.8)
    kind, conf = fuse_verdict("hole", 0.5, verdict)
    assert kind == "hole"
    assert conf == pytest.approx(0.5 * 0.5 + 0.5 * 0.8)


def test_fuse_vlm_refines_freeform():
    verdict = VlmVerdict(is_cutout=True, kind="notch", confidence=0.9)
    kind, _ = fuse_verdict("freeform", 0.3, verdict)
    assert kind == "notch"


def test_fuse_clamps_to_max():
    verdict = VlmVerdict(is_cutout=True, kind="hole", confidence=1.0)
    _, conf = fuse_verdict("hole", 1.0, verdict)
    assert conf == 0.98


# --- select_for_escalation ---


@pytest.mark.parametrize(
    "raw",
    [
        '{"is_cutout": true}',
        '```json\n{"is_cutout": true}\n```',
        '```\n{"is_cutout": true}\n```',
        '  ```json\n{"is_cutout": true}\n```  ',
    ],
)
def test_strip_fences(raw):
    assert _strip_fences(raw) == '{"is_cutout": true}'


def test_select_only_sub_threshold(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "escalation_threshold", 0.65)
    monkeypatch.setattr(settings, "vlm_max_calls_per_page", 15)
    scores = [0.9, 0.6, 0.3, 0.64, 0.65]
    assert select_for_escalation(scores) == [3, 1, 2]


def test_select_caps_per_page(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "escalation_threshold", 0.65)
    monkeypatch.setattr(settings, "vlm_max_calls_per_page", 2)
    scores = [0.1, 0.5, 0.3]
    assert select_for_escalation(scores) == [1, 2]


# --- integration with mocked client ---


@pytest.fixture
def mock_vlm(monkeypatch):
    calls = []

    def fake_classify(self, crop_png: bytes, prompt: str | None = None) -> VlmResult:
        calls.append(len(crop_png))
        return VlmResult(
            verdict=VlmVerdict(is_cutout=True, kind="hole", confidence=0.9),
            raw_response='{"is_cutout": true, "kind": "hole", "confidence": 0.9}',
            latency_ms=1,
            prompt_hash="deadbeef",
        )

    monkeypatch.setattr(OllamaVlmClient, "available", lambda self: True)
    monkeypatch.setattr(OllamaVlmClient, "classify_crop", fake_classify)
    return calls


def _upload(client, name: str) -> dict:
    with open(PDFS_DIR / name, "rb") as f:
        r = client.post(
            "/api/documents", files={"file": (name, f, "application/pdf")}
        )
    if r.status_code == 409:  # already ingested by an earlier test in this module
        doc_id = int(r.json()["detail"].split("id=")[1].rstrip(")"))
        return client.get(f"/api/documents/{doc_id}").json()
    assert r.status_code == 201
    return r.json()


@pytest.mark.skipif(not (PDFS_DIR / DOC3).exists(), reason="sample pdf missing")
def test_vlm_escalation_pipeline(client, wait_job, mock_vlm):
    import app.db.session as db_session
    from app.config import settings
    from app.db.models import Cutout, VlmCall

    doc = _upload(client, DOC3)
    r = client.post(f"/api/documents/{doc['id']}/jobs", json={"vlm": True})
    assert r.status_code == 202
    job = wait_job(client, r.json()["id"])
    assert job["status"] == "done", job["error"]

    with db_session.SessionLocal() as db:
        vlm_calls = db.query(VlmCall).filter_by(job_id=job["id"]).all()
        assert len(vlm_calls) == len(mock_vlm), (
            f"{len(mock_vlm)} calls made but {len(vlm_calls)} audit rows written"
        )
        assert all(c.ok for c in vlm_calls)
        assert all(Path(c.crop_path).exists() for c in vlm_calls)

        # Two passes: RESCUE the doubtful, and VETO the confident. The confident ones are
        # where the errors the operator actually sees live — a GD&T frame scores 0.98, and
        # escalation would never have shown it to the model at all.
        #
        # The VETO pass must always run. The RESCUE pass may legitimately have nothing to
        # do: on this drawing the extractor now produces 17 candidates and every one of
        # them is a real cutout, so nothing falls below the escalation threshold. A clean
        # detector makes the rescue pass idle — that is the goal, not a failure.
        by_trigger = {c.trigger for c in vlm_calls}
        assert "verification" in by_trigger

        escalated = [c for c in vlm_calls if c.trigger == "low_confidence"]
        assert len(escalated) <= settings.vlm_max_calls_per_page

        # verification is grouped: one question per distinct shape+size, not per cutout
        verified = [c for c in vlm_calls if c.trigger == "verification"]
        approved = db.query(Cutout).filter(
            Cutout.page_id.in_([p["id"] for p in doc["pages"]]),
            Cutout.confidence >= settings.finalize_threshold,
        ).count()
        assert 0 < len(verified) < approved, "identical holes must share one call"

        fused = (
            db.query(Cutout)
            .filter_by(job_id=job["id"], source="fused")
            .all()
        )
        # Only the ESCALATED cutouts are fused. A verification that CONFIRMS deliberately
        # changes nothing: averaging the model's own confidence in would demote a real
        # hole for the crime of the model being only 60% sure of something it got right.
        # The model objects; it does not grade. (This mock always confirms, so the veto
        # path writes nothing here — see test_verify.py for that.)
        assert len(fused) == len(escalated)
        assert all(c.confidence <= 0.98 for c in fused)


@pytest.mark.skipif(not (PDFS_DIR / DOC3).exists(), reason="sample pdf missing")
def test_vlm_off_by_default(client, wait_job, mock_vlm):
    import app.db.session as db_session
    from app.db.models import VlmCall

    doc = _upload(client, DOC3)
    r = client.post(f"/api/documents/{doc['id']}/jobs")
    assert r.status_code == 202
    job = wait_job(client, r.json()["id"])
    assert job["status"] == "done"

    with db_session.SessionLocal() as db:
        assert db.query(VlmCall).filter_by(job_id=job["id"]).count() == 0
    assert mock_vlm == []


def test_vlm_unavailable_degrades(client, wait_job, monkeypatch):
    monkeypatch.setattr(OllamaVlmClient, "available", lambda self: False)
    name = "A (3).pdf"
    if not (PDFS_DIR / name).exists():
        pytest.skip("sample pdf missing")
    doc = _upload(client, name)
    r = client.post(f"/api/documents/{doc['id']}/jobs", json={"vlm": True})
    assert r.status_code == 202
    job = wait_job(client, r.json()["id"])
    assert job["status"] == "done"
