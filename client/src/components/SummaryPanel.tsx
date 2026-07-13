import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { SummaryBucket, TelemetrySummary } from "../api/types";

function StatRow({ name, b }: { name: string; b: SummaryBucket }) {
  return (
    <tr className="border-t border-zinc-800">
      <td className="py-1 pr-3 text-zinc-300">{name}</td>
      <td className="px-2 text-center text-zinc-400">{b.total}</td>
      <td className="px-2 text-center">{b.pending}</td>
      <td className="px-2 text-center text-emerald-400">{b.approved}</td>
      <td className="px-2 text-center text-red-400">{b.rejected}</td>
      <td className="px-2 text-center text-blue-400">{b.edited}</td>
      <td className="px-2 text-center">
        {b.approve_rate == null ? "—" : `${(b.approve_rate * 100).toFixed(0)}%`}
      </td>
    </tr>
  );
}

function Head({ first }: { first: string }) {
  return (
    <thead>
      <tr className="text-zinc-500">
        <th className="pb-1 pr-3 text-left">{first}</th>
        <th>total</th>
        <th>pending</th>
        <th>appr</th>
        <th>rej</th>
        <th>edit</th>
        <th>rate</th>
      </tr>
    </thead>
  );
}

export default function SummaryPanel({
  docId,
  onClose,
}: {
  /** Scopes the stats to one document. Without it the panel silently reports the
   *  whole database, which reads as this document's numbers and is not. */
  docId?: number;
  onClose: () => void;
}) {
  const [summary, setSummary] = useState<TelemetrySummary | null>(null);

  useEffect(() => {
    api.telemetrySummary(docId).then(setSummary).catch(() => {});
  }, [docId]);

  return (
    <div
      className="fixed inset-0 z-20 flex items-center justify-center bg-black/70"
      onClick={onClose}
    >
      <div
        className="max-h-[80vh] w-[560px] overflow-auto rounded-lg border border-zinc-700 bg-zinc-950 p-5"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-3 flex items-center justify-between">
          <div>
            <h2 className="text-lg font-semibold">Telemetry summary</h2>
            <p className="text-xs text-zinc-500">
              {docId == null ? "All documents" : "This document only"}
            </p>
          </div>
          <button onClick={onClose} className="text-zinc-500 hover:text-zinc-300">
            ✕
          </button>
        </div>
        {!summary ? (
          <p className="text-sm text-zinc-500">Loading…</p>
        ) : (
          <div className="space-y-5 text-xs">
            <div>
              <h3 className="mb-1 font-medium text-zinc-400">
                Approve rate by source
              </h3>
              <table className="w-full">
                <Head first="source" />
                <tbody>
                  {Object.entries(summary.by_source).map(([source, b]) => (
                    <StatRow key={source} name={source} b={b} />
                  ))}
                </tbody>
              </table>
            </div>
            <div>
              <h3 className="mb-1 font-medium text-zinc-400">
                Approve rate by confidence (escalation threshold{" "}
                {summary.escalation_threshold})
              </h3>
              <table className="w-full">
                <Head first="bucket" />
                <tbody>
                  {summary.by_confidence.map((b) => (
                    <StatRow key={b.bucket} name={b.bucket!} b={b} />
                  ))}
                </tbody>
              </table>
            </div>
            <div className="text-zinc-400">
              VLM: {summary.vlm.calls} calls
              {summary.vlm.ok_rate != null &&
                ` · ${(summary.vlm.ok_rate * 100).toFixed(0)}% ok`}
              {summary.vlm.avg_latency_ms != null &&
                ` · avg ${Math.round(summary.vlm.avg_latency_ms)}ms`}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
