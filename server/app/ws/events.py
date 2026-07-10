import asyncio
from collections import defaultdict

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.db import session as db_session
from app.db.models import ExtractionJob

TERMINAL_EVENTS = {"job_done", "job_failed"}


class JobEventBroker:
    """Fan-out of job progress events to WebSocket subscribers.

    The pipeline runs in a worker thread, so publishes hop onto the event
    loop via call_soon_threadsafe. Per-job history lets late subscribers
    replay everything they missed.
    """

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._history: dict[int, list[dict]] = defaultdict(list)
        self._subs: dict[int, set[asyncio.Queue]] = defaultdict(set)

    def reset(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._history.clear()
        self._subs.clear()

    def publish_threadsafe(self, job_id: int, event: dict) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._publish, job_id, event)

    def _publish(self, job_id: int, event: dict) -> None:
        self._history[job_id].append(event)
        for q in self._subs[job_id]:
            q.put_nowait(event)

    def subscribe(self, job_id: int) -> tuple[list[dict], asyncio.Queue]:
        q: asyncio.Queue = asyncio.Queue()
        self._subs[job_id].add(q)
        return list(self._history[job_id]), q

    def unsubscribe(self, job_id: int, q: asyncio.Queue) -> None:
        self._subs[job_id].discard(q)


broker = JobEventBroker()

router = APIRouter()


@router.websocket("/ws/jobs/{job_id}")
async def job_events(ws: WebSocket, job_id: int) -> None:
    await ws.accept()
    with db_session.SessionLocal() as db:
        job = db.get(ExtractionJob, job_id)
    if job is None:
        await ws.close(code=4404, reason="Job not found")
        return

    history, q = broker.subscribe(job_id)
    try:
        terminal = False
        for event in history:
            await ws.send_json(event)
            terminal = terminal or event["type"] in TERMINAL_EVENTS
        if not terminal and not history and job.status in ("done", "failed"):
            # job predates this server run — no history, only the DB state
            await ws.send_json(
                {"type": f"job_{job.status}", "job_id": job_id, "error": job.error}
            )
            terminal = True
        while not terminal:
            event = await q.get()
            await ws.send_json(event)
            terminal = event["type"] in TERMINAL_EVENTS
        await ws.close()
    except WebSocketDisconnect:
        pass
    finally:
        broker.unsubscribe(job_id, q)
