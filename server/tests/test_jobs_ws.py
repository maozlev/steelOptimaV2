from pathlib import Path

import pytest

PDFS_DIR = Path(__file__).parent.parent.parent / "pdfs"


def _upload(client, name: str) -> dict:
    pdf = PDFS_DIR / name
    r = client.post(
        "/api/documents",
        files={"file": (pdf.name, pdf.read_bytes(), "application/pdf")},
    )
    assert r.status_code == 201
    return r.json()


def _collect_events(client, job_id: int) -> list[dict]:
    events = []
    with client.websocket_connect(f"/ws/jobs/{job_id}") as ws:
        while True:
            event = ws.receive_json()
            events.append(event)
            if event["type"] in ("job_done", "job_failed"):
                return events


def test_ws_streams_job_lifecycle(client):
    doc = _upload(client, "A (3).pdf")
    job = client.post(f"/api/documents/{doc['id']}/jobs").json()

    events = _collect_events(client, job["id"])
    types = [e["type"] for e in events]
    assert types[0] == "job_started"
    assert types[-1] == "job_done"
    assert types.count("page_started") == doc["page_count"]
    page_done = [e for e in events if e["type"] == "page_done"]
    assert len(page_done) == doc["page_count"]
    assert sum(e["candidates"] for e in page_done) > 0

    final = client.get(f"/api/jobs/{job['id']}").json()
    assert final["status"] == "done"


def test_ws_replays_history_after_completion(client, wait_job):
    doc = _upload(client, "A (4).pdf")
    job = client.post(f"/api/documents/{doc['id']}/jobs").json()
    wait_job(client, job["id"])

    events = _collect_events(client, job["id"])
    types = [e["type"] for e in events]
    assert types[0] == "job_started"
    assert types[-1] == "job_done"


def test_ws_unknown_job_rejected(client):
    with pytest.raises(Exception):
        with client.websocket_connect("/ws/jobs/99999") as ws:
            ws.receive_json()


def test_queued_jobs_run_in_order(client, wait_job):
    doc = _upload(client, "Doc_HK3573_290626083217_00 (1).pdf")
    first = client.post(f"/api/documents/{doc['id']}/jobs").json()
    second = client.post(f"/api/documents/{doc['id']}/jobs").json()
    assert first["status"] == "queued"

    done_second = wait_job(client, second["id"])
    done_first = client.get(f"/api/jobs/{first['id']}").json()
    assert done_first["status"] == "done"
    assert done_second["status"] == "done"
    assert done_first["finished_at"] <= done_second["started_at"]
