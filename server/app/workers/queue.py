import asyncio

from app.extraction.service import execute_job
from app.ws.events import broker


class JobWorker:
    """Single asyncio worker draining the job queue one job at a time.

    Extraction is CPU-bound (OpenCV, OCR), so each job runs in a thread and
    the event loop stays free to serve requests and stream WS events.
    """

    def __init__(self) -> None:
        self._queue: asyncio.Queue[int] = asyncio.Queue()
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._queue = asyncio.Queue()
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    def enqueue(self, job_id: int) -> None:
        self._queue.put_nowait(job_id)

    async def _run(self) -> None:
        while True:
            job_id = await self._queue.get()
            emit = lambda e: broker.publish_threadsafe(job_id, e)  # noqa: E731
            try:
                await asyncio.to_thread(execute_job, job_id, emit)
            except Exception as e:  # execute_job records its own failures
                emit({"type": "job_failed", "job_id": job_id, "error": str(e)})


worker = JobWorker()
