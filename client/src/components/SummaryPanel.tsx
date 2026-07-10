import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { SummaryBucket, TelemetrySummary } from "../api/types";

function StatRow({ name, b }: { name: string; b: SummaryBucket }) {
  return (
    <tr className="border-t border-zinc-800">
      <td className="py-1 pr-3 text-zinc-300">{name}</td>
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

export default function SummaryPanel({ onClose }: { onClose: () => void }) {
  const [summary, setSummary] = useState<TelemetrySummary | null>(null);

  useEffect(() => {
    api.telemetrySummary().then(setSummary).catch(() => {});
  }, []);

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
          <h2 className="text-lg font-semibold">Telemetry summary</h2>
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
                <thead>
                  <tr className="text-zinc-500">
                    <th className="pb-1 pr-3 text-left">source</th>
                    <th>pending</th>
                    <th>appr</th>
                    <th>rej</th>
                    <th>edit</th>
                    <th>rate</th>
                  </tr>
                </thead>
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
                <thead>
                  <tr className="text-zinc-500">
                    <th className="pb-1 pr-3 text-left">bucket</th>
                    <th>pending</th>
                    <th>appr</th>
                    <th>rej</th>
                    <th>edit</th>
                    <th>rate</th>
                  </tr>
                </thead>
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
