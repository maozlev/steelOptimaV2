from pathlib import Path

import pytest
from fastapi.testclient import TestClient

PDFS_DIR = Path(__file__).parent.parent.parent / "pdfs"


@pytest.fixture
def wait_job():
    import time

    def _wait(client, job_id: int, timeout: float = 300.0) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            job = client.get(f"/api/jobs/{job_id}").json()
            if job["status"] in ("done", "failed"):
                return job
            time.sleep(0.2)
        raise TimeoutError(f"job {job_id} did not finish within {timeout}s")

    return _wait


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    from app.config import settings

    settings.data_dir = tmp_path_factory.mktemp("data")
    settings.ensure_dirs()
    # keep pipeline tests offline/deterministic; VLM tests opt in per job
    settings.vlm_enabled = False
    # uploads must not fire OCR jobs behind the API tests' backs
    settings.table_autorun_on_upload = False

    import app.db.session as db_session
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db.models import Base

    db_session.engine = create_engine(
        f"sqlite:///{settings.db_path}", connect_args={"check_same_thread": False}
    )
    db_session.SessionLocal = sessionmaker(
        bind=db_session.engine, expire_on_commit=False
    )
    Base.metadata.create_all(db_session.engine)

    from app.main import app

    with TestClient(app) as c:
        yield c
