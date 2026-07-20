import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { ProjectQueueOut } from "../api/types";

/** m:ss of real elapsed time — a fact, not a prediction. Scan time swings from
 *  under a second to nearly two minutes depending on the drawing, so there is no
 *  honest "time left"; we show how long the running scan has actually been going. */
function fmtElapsed(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/** Live scan-queue for one project. It ONLY exists while there is real work in
 *  flight (uploading / running / queued) or a failure to retry — a project of
 *  already-scanned documents shows nothing here; the per-document badges carry
 *  the "done" state. Documents that were never asked to scan are NOT a queue. */
export default function QueuePanel({
  projectId,
  uploading,
  onChanged,
}: {
  projectId: number;
  /** files still being uploaded by the dropzone pump — on their way into the queue */
  uploading: { done: number; total: number } | null;
  onChanged: () => void;
}) {
  const [queue, setQueue] = useState<ProjectQueueOut | null>(null);
  const [showWaiting, setShowWaiting] = useState(false);
  const [busy, setBusy] = useState(false);
  const lastActive = useRef(false);

  const refresh = useCallback(
    () => api.getProjectQueue(projectId).then(setQueue).catch(() => {}),
    [projectId],
  );

  useEffect(() => {
    refresh();
  }, [refresh, uploading?.done]);

  const uploadsInFlight = uploading ? uploading.total - uploading.done : 0;
  // "active" is genuine in-flight work — NOT documents that merely lack a scan
  const active =
    uploadsInFlight > 0 ||
    !!queue?.running.length ||
    !!queue?.queued.length;

  // poll while active; one settling refresh when work ends
  useEffect(() => {
    if (!active) {
      if (lastActive.current) {
        refresh();
        onChanged();
      }
      lastActive.current = false;
      return;
    }
    lastActive.current = true;
    const t = window.setInterval(refresh, 2000);
    return () => window.clearInterval(t);
  }, [active, refresh, onChanged]);

  // Live "elapsed" clock for the running scan. The server hands us the true
  // elapsed at each 2s poll (measured on its own UTC clock — the client's clock
  // and timezone never enter into it); between polls we tick forward locally off
  // a monotonic anchor so the number moves every second instead of jumping.
  const runningNow = queue?.running[0] ?? null;
  const anchor = useRef<{ base: number; at: number } | null>(null);
  useEffect(() => {
    anchor.current =
      runningNow?.elapsed_seconds != null
        ? { base: runningNow.elapsed_seconds, at: performance.now() }
        : null;
  }, [runningNow?.job_id, runningNow?.elapsed_seconds]);
  const [, tick] = useState(0);
  useEffect(() => {
    if (!runningNow) return;
    const t = window.setInterval(() => tick((n) => n + 1), 1000);
    return () => window.clearInterval(t);
  }, [runningNow?.job_id]);
  const liveElapsed = anchor.current
    ? anchor.current.base + (performance.now() - anchor.current.at) / 1000
    : null;

  if (!queue) return null;

  const failed = queue.failed;

  // Idle and nothing failed → no panel at all. This is the "don't look like a
  // queue when there is no queue" rule: a project full of scanned (or never-
  // scanned) documents shows nothing here.
  if (!active && failed.length === 0) return null;

  async function cancelOne(jobId: number) {
    setBusy(true);
    try {
      await api.cancelJob(jobId);
      await refresh();
      onChanged();
    } finally {
      setBusy(false);
    }
  }

  async function cancelAllQueued() {
    if (!queue) return;
    setBusy(true);
    try {
      await Promise.allSettled(queue.queued.map((e) => api.cancelJob(e.job_id)));
      await refresh();
      onChanged();
    } finally {
      setBusy(false);
    }
  }

  async function retryFailed() {
    setBusy(true);
    try {
      await api.startProjectTableJobs(projectId, true);
      await refresh();
      onChanged();
    } finally {
      setBusy(false);
    }
  }

  // Failures only, nothing running → compact actionable strip, no progress bar,
  // no "scanning" language.
  if (!active) {
    return (
      <div className="rounded border border-red-900/60 bg-red-950/30 px-4 py-2.5">
        <div className="flex items-center justify-between">
          <span className="text-sm font-medium text-red-300">
            {failed.length} scan{failed.length === 1 ? "" : "s"} failed
          </span>
          <button
            onClick={retryFailed}
            disabled={busy}
            className="rounded bg-zinc-800 px-2.5 py-1 text-xs hover:bg-zinc-700 disabled:opacity-40"
          >
            ↻ retry failed
          </button>
        </div>
        <ul className="mt-1.5 text-xs text-red-300/80">
          {failed.map((e) => (
            <li key={e.job_id} className="truncate">
              {e.filename}
              {e.error && <span className="text-red-400/60"> — {e.error}</span>}
            </li>
          ))}
        </ul>
      </div>
    );
  }

  // Active scan. Denominator is the batch actually going through the pipeline —
  // scanned + failed + running + queued + uploads — never the whole project, so
  // never-scanned hole drawings don't drag the bar to 0%.
  const inPipeline =
    queue.scanned +
    failed.length +
    queue.running.length +
    queue.queued.length +
    uploadsInFlight;
  const doneCount = queue.scanned;
  const waitingCount = queue.queued.length + uploadsInFlight;
  const pct = inPipeline > 0 ? Math.round((doneCount / inPipeline) * 100) : 0;

  return (
    <div className="rounded border border-zinc-700 bg-zinc-900/70 px-4 py-3">
      <div className="mb-1.5 flex items-baseline justify-between">
        <span className="text-sm font-medium">Scanning…</span>
        <span className="text-sm tabular-nums text-zinc-300">
          {doneCount} / {inPipeline} scanned
        </span>
      </div>
      <div className="h-2 w-full overflow-hidden rounded bg-zinc-800">
        <div
          className="h-full rounded bg-emerald-600 transition-all duration-700"
          style={{ width: `${pct}%` }}
        />
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-zinc-400">
        {uploadsInFlight > 0 && (
          <span className="text-sky-300">
            ⇧ uploading {uploading!.done + 1}/{uploading!.total}
          </span>
        )}
        {runningNow && (
          <span className="text-sky-300">
            <span className="mr-1 inline-block animate-pulse">●</span>
            scanning <span className="text-zinc-200">{runningNow.filename}</span>
            {liveElapsed != null && (
              <span className="ml-1.5 tabular-nums text-zinc-500">
                {fmtElapsed(liveElapsed)}
              </span>
            )}
          </span>
        )}
        {waitingCount > 0 && (
          <button
            onClick={() => setShowWaiting((s) => !s)}
            className="underline decoration-dotted underline-offset-2 hover:text-zinc-200"
          >
            ⏳ {waitingCount} waiting {showWaiting ? "▴" : "▾"}
          </button>
        )}
        {failed.length > 0 && (
          <span className="text-red-400">⚠ {failed.length} failed</span>
        )}
        <span className="ml-auto flex gap-2">
          {queue.queued.length > 0 && (
            <button
              onClick={cancelAllQueued}
              disabled={busy}
              className="rounded bg-zinc-800 px-2 py-0.5 hover:bg-red-900 disabled:opacity-40"
            >
              ✕ cancel queued
            </button>
          )}
          {failed.length > 0 && (
            <button
              onClick={retryFailed}
              disabled={busy}
              className="rounded bg-zinc-800 px-2 py-0.5 hover:bg-zinc-700 disabled:opacity-40"
            >
              ↻ retry failed
            </button>
          )}
        </span>
      </div>

      {showWaiting && queue.queued.length > 0 && (
        <ul className="mt-2 divide-y divide-zinc-800/60 rounded border border-zinc-800 bg-zinc-950/40 text-xs">
          {queue.queued.map((e) => (
            <li key={e.job_id} className="flex items-center justify-between px-2 py-1">
              <span className="truncate text-zinc-300">
                <span className="mr-2 text-zinc-600">#{e.queue_position}</span>
                {e.filename}
              </span>
              <button
                onClick={() => cancelOne(e.job_id)}
                disabled={busy}
                title="Remove from queue"
                className="ml-2 rounded px-1.5 text-zinc-500 hover:bg-zinc-800 hover:text-red-400"
              >
                ✕
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
