"""Route a queued job to its pipeline by ExtractionJob.kind.

Lives apart from queue.py so the worker does not import both pipelines at
startup, and apart from the services to avoid an import cycle. Anything that is
not explicitly "tables" — including the "" that add_missing_columns backfills
onto pre-existing rows — runs the original cutout extraction.
"""

from app.db import session as db_session
from app.db.models import ExtractionJob


def execute(job_id: int, emit) -> None:
    with db_session.SessionLocal() as db:
        job = db.get(ExtractionJob, job_id)
        kind = job.kind if job else None

    if kind == "tables":
        from app.tables.service import execute_table_job

        execute_table_job(job_id, emit)
    else:
        from app.extraction.service import execute_job

        execute_job(job_id, emit)
