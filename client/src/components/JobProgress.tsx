import type { JobEvent } from "../api/types";

export default function JobProgress({
  events,
  pageCount,
}: {
  events: JobEvent[];
  pageCount: number;
}) {
  if (!events.length) return null;
  const last = events[events.length - 1];
  const pagesDone = events.filter((e) => e.type === "page_done").length;
  const vlmCalls = events.filter((e) => e.type === "vlm_call").length;
  const vlmUnavailable = events.some((e) => e.type === "vlm_unavailable");
  const failed = last.type === "job_failed";
  const done = last.type === "job_done";

  let label: string;
  if (failed) label = `Job failed: ${String(last.error ?? "unknown error")}`;
  else if (done) label = `Extraction done — ${pagesDone}/${pageCount} pages`;
  else if (last.type === "vlm_call")
    label = `VLM reviewing candidates… (${vlmCalls} calls)`;
  else if (last.type === "page_started")
    label = `Extracting page ${Number(last.page_index) + 1}/${pageCount} (${String(last.kind)})…`;
  else label = "Job running…";

  return (
    <div
      className={`flex items-center gap-3 border-b px-4 py-1.5 text-xs ${
        failed
          ? "border-red-900 bg-red-950/60 text-red-300"
          : done
            ? "border-emerald-900 bg-emerald-950/40 text-emerald-300"
            : "border-zinc-800 bg-zinc-900 text-zinc-300"
      }`}
    >
      {!done && !failed && (
        <span className="h-2 w-2 animate-pulse rounded-full bg-amber-400" />
      )}
      <span className="flex-1">{label}</span>
      {vlmCalls > 0 && <span className="text-zinc-500">VLM calls: {vlmCalls}</span>}
      {vlmUnavailable && (
        <span className="text-amber-400">VLM unavailable — CV only</span>
      )}
      <span className="text-zinc-500">
        {pagesDone}/{pageCount} pages
      </span>
    </div>
  );
}
