import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { ProjectQueueOut } from "../api/types";

function humanEta(seconds: number): string {
  if (seconds < 90) return `~${Math.max(Math.round(seconds / 10) * 10, 10)}s left`;
  if (seconds < 3600) return `~${Math.round(seconds / 60)} min left`;
  return `~${(seconds / 3600).toFixed(1)} h left`;
}

/** Live scan-queue for one project: progress bar, what's scanning now, what's
 *  waiting (cancellable), what failed (retryable). Polls only while active. */
export default function QueuePanel({
  projectId,
  uploading,
  onChanged,
}: {
  projectId: number;
  /** files still being uploaded by the dropzone pump — part of the pipeline
   *  the user cares about, so the panel counts them as "on their way" */
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

  const active =
    !!uploading ||
    !!queue?.running.length ||
    !!queue?.queued.length;

  // poll while active; one extra refresh when activity ends so counts settle
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

  if (!queue) return null;

  const uploadsInFlight = uploading ? uploading.total - uploading.done : 0;
  const total = queue.total_documents + uploadsInFlight;
  const doneCount = queue.scanned;
  const failedCount = queue.failed.length;
  const waitingCount =
    queue.queued.length + queue.unscanned.length + uploadsInFlight;
  const runningNow = queue.running[0];

  // idle and nothing worth saying
  if (!active && failedCount === 0 && total === 0) return null;
  if (!active && failedCount === 0 && doneCount === total) {
    return (
      <div className="flex items-center gap-2 rounded border border-emerald-900/60 bg-emerald-950/30 px-3 py-2 text-sm text-emerald-300">
        ✓ All {total} document{total === 1 ? "" : "s"} scanned
      </div>
    );
  }

  const pct = total > 0 ? Math.round((doneCount / total) * 100) : 0;

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

  return (
    <div className="rounded border border-zinc-700 bg-zinc-900/70 px-4 py-3">
      {/* headline + progress */}
      <div className="mb-1.5 flex items-baseline justify-between">
        <span className="text-sm font-medium">
          {active ? "Scanning project…" : "Scan finished"}
        </span>
        <span className="text-sm tabular-nums text-zinc-300">
          {doneCount} / {total} scanned
          {queue.eta_seconds != null && active && (
            <span className="ml-2 text-zinc-500">
              {humanEta(queue.eta_seconds)}
            </span>
          )}
        </span>
      </div>
      <div className="h-2 w-full overflow-hidden rounded bg-zinc-800">
        <div
          className="h-full rounded bg-emerald-600 transition-all duration-700"
          style={{ width: `${pct}%` }}
        />
      </div>

      {/* live line: what is happening right now */}
      <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-zinc-400">
        {uploading && (
          <span className="text-sky-300">
            ⇧ uploading {uploading.done + 1}/{uploading.total}
          </span>
        )}
        {runningNow && (
          <span className="text-sky-300">
            <span className="mr-1 inline-block animate-pulse">●</span>
            scanning <span className="text-zinc-200">{runningNow.filename}</span>
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
        {failedCount > 0 && (
          <span className="text-red-400">⚠ {failedCount} failed</span>
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
          {failedCount > 0 && (
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

      {/* expandable waiting list with per-item cancel */}
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

      {/* failed list with reasons */}
      {failedCount > 0 && (
        <ul className="mt-2 text-xs text-red-300/80">
          {queue.failed.map((e) => (
            <li key={e.job_id} className="truncate">
              ⚠ {e.filename}
              {e.error && <span className="text-red-400/60"> — {e.error}</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
