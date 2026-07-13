from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api import bom, cutouts, documents, export, health, jobs, telemetry
from app.config import settings
from app.db import session as db_session
from app.db.migrate import add_missing_columns
from app.db.models import Base, ExtractionJob
from app.workers.queue import worker
from app.ws import events


def _fail_orphaned_jobs() -> None:
    with db_session.SessionLocal() as db:
        db.query(ExtractionJob).filter(
            ExtractionJob.status.in_(["queued", "running"])
        ).update({"status": "failed", "error": "server restarted"})
        db.commit()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings.ensure_dirs()
    Base.metadata.create_all(db_session.engine)
    # new columns on existing tables — so a schema change never means wiping the
    # operator's database and re-reviewing every drawing
    add_missing_columns(db_session.engine, Base)
    _fail_orphaned_jobs()
    events.broker.reset()
    worker.start()
    yield
    await worker.stop()


app = FastAPI(title="SteelOptima Server", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(documents.router)
app.include_router(health.router)
app.include_router(jobs.router)
app.include_router(cutouts.router)
app.include_router(bom.router)
app.include_router(export.router)
app.include_router(telemetry.router)
app.include_router(events.router)

_client_dist = Path(__file__).resolve().parents[2] / "client" / "dist"
if _client_dist.exists():
    app.mount("/", StaticFiles(directory=_client_dist, html=True), name="client")
