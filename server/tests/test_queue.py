"""Per-project scan-queue management: visibility, cancel, retry, restart recovery.

The worker itself stays a single FIFO drain; these tests freeze it (enqueue
no-op) so queued jobs hold still long enough to assert on queue shape.
"""

from pathlib import Path

import pytest

TABLES_DIR = Path(__file__).parent.parent.parent / "tables"


@pytest.fixture()
def frozen_worker(monkeypatch):
    from app.workers.queue import worker

    monkeypatch.setattr(worker, "enqueue", lambda job_id: None)
    return worker


@pytest.fixture(scope="module")
def project(client):
    project_id = client.post("/api/projects", json={"name": "Queue test"}).json()["id"]
    for name in ("833.1-01-20.pdf", "833.1-02-20.pdf"):
        with open(TABLES_DIR / name, "rb") as f:
            r = client.post(
                f"/api/projects/{project_id}/documents",
                files={"file": (name, f, "application/pdf")},
            )
            assert r.status_code == 201
    return project_id


def test_queue_lifecycle(client, project, frozen_worker):
    # nothing scanned yet
    q = client.get(f"/api/projects/{project}/queue").json()
    assert q["total_documents"] == 2
    assert q["scanned"] == 0 and not q["queued"] and not q["running"]
    assert len(q["unscanned"]) == 2

    # enqueue everything (worker frozen -> stays queued)
    jobs = client.post(f"/api/projects/{project}/table-jobs").json()
    assert len(jobs) == 2

    q = client.get(f"/api/projects/{project}/queue").json()
    assert [e["queue_position"] for e in q["queued"]] == [1, 2]
    assert not q["unscanned"]

    # re-trigger while queued: nothing doubles
    assert client.post(f"/api/projects/{project}/table-jobs").json() == []

    # cancel the first queued job
    first = q["queued"][0]
    r = client.delete(f"/api/jobs/{first['job_id']}")
    assert r.status_code == 200
    assert r.json()["status"] == "failed"

    q = client.get(f"/api/projects/{project}/queue").json()
    assert len(q["queued"]) == 1
    assert len(q["failed"]) == 1
    assert q["failed"][0]["error"] == "cancelled by user"
    # the survivor moved up the global line
    assert q["queued"][0]["queue_position"] == 1

    # cancel is queued-only: the already-cancelled job 409s
    assert client.delete(f"/api/jobs/{first['job_id']}").status_code == 409

    # retry failed re-queues ONLY the cancelled document
    retried = client.post(
        f"/api/projects/{project}/table-jobs?only_failed=true"
    ).json()
    assert len(retried) == 1
    assert retried[0]["document_id"] == first["document_id"]

    q = client.get(f"/api/projects/{project}/queue").json()
    assert len(q["queued"]) == 2 and not q["failed"]


def test_scan_all_skips_already_scanned(client, project, frozen_worker):
    """A project-wide scan must not re-burn documents that already scanned
    clean — only force=true redoes them."""
    import app.db.session as db_session
    from app.db.models import ExtractionJob

    # clear the queue from the previous test and pretend both scanned clean
    with db_session.SessionLocal() as db:
        pids = [d for (d,) in db.query(ExtractionJob.id)]
        db.query(ExtractionJob).filter(ExtractionJob.id.in_(pids)).update(
            {"status": "done"}, synchronize_session=False
        )
        db.commit()

    # default scan-all: nothing to do, everything already done
    assert client.post(f"/api/projects/{project}/table-jobs").json() == []
    # force: re-scans every document. These two stay queued (worker frozen) and
    # become the fixture the restart-recovery test below relies on.
    forced = client.post(f"/api/projects/{project}/table-jobs?force=true").json()
    assert len(forced) == 2


def test_cutouts_project_scans_for_cutouts(client, frozen_worker):
    """The project's kind decides the pipeline: a holes & shapes project must
    create CUTOUT jobs, and its queue must ignore table jobs entirely."""
    r = client.post(
        "/api/projects", json={"name": "Shapes", "kind": "cutouts"}
    )
    assert r.status_code == 201 and r.json()["kind"] == "cutouts"
    pid = r.json()["id"]

    pdf = TABLES_DIR / "833.1-04-20.pdf"
    with open(pdf, "rb") as f:
        doc = client.post(
            f"/api/projects/{pid}/documents",
            files={"file": (pdf.name, f, "application/pdf")},
        ).json()

    jobs = client.post(f"/api/projects/{pid}/table-jobs").json()
    assert len(jobs) == 1

    import app.db.session as db_session
    from app.db.models import ExtractionJob

    with db_session.SessionLocal() as db:
        job = db.get(ExtractionJob, jobs[0]["id"])
        assert job.kind != "tables"  # a cutout job, not a table scan

    q = client.get(f"/api/projects/{pid}/queue").json()
    assert len(q["queued"]) == 1
    assert q["queued"][0]["document_id"] == doc["id"]

    # invalid kind is rejected
    assert (
        client.post("/api/projects", json={"name": "x", "kind": "bogus"}).status_code
        == 422
    )


def test_restart_recovery_requeues_queued(client, project, frozen_worker):
    """Queued jobs survive a restart; a job caught running fails honestly."""
    import app.db.session as db_session
    from app.db.models import ExtractionJob
    from app.main import _recover_orphaned_jobs

    with db_session.SessionLocal() as db:
        queued_ids = [
            j
            for (j,) in db.query(ExtractionJob.id).filter(
                ExtractionJob.status == "queued"
            )
        ]
        assert queued_ids, "test needs queued jobs from the previous test"
        victim = queued_ids[0]
        db.query(ExtractionJob).filter(ExtractionJob.id == victim).update(
            {"status": "running"}
        )
        db.commit()

    requeue = _recover_orphaned_jobs()
    assert victim not in requeue
    assert set(requeue) == set(queued_ids[1:])

    job = client.get(f"/api/jobs/{victim}").json()
    assert job["status"] == "failed"
    assert "restarted" in job["error"]
