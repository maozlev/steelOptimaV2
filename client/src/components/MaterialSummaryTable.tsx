import type { ProjectSummary } from "../api/types";

function csvEscape(v: string | number | null): string {
  const s = v == null ? "" : String(v);
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

export function exportSummaryCsv(summary: ProjectSummary, filename: string) {
  const header = [
    "material_key",
    "description",
    "qty",
    "total_length_m",
    "total_weight_kg",
    "lengths",
    "documents",
    "projects",
  ];
  const lines = [header.join(",")];
  for (const r of summary.rows) {
    lines.push(
      [
        csvEscape(r.material_key),
        csvEscape(r.description),
        r.qty,
        (r.total_length_mm / 1000).toFixed(2),
        r.total_weight_kg,
        csvEscape(
          r.lengths.map((l) => `${l.qty}x${l.unit_length_mm}mm`).join(" | "),
        ),
        csvEscape(r.documents.join(" | ")),
        csvEscape(r.projects.join(" | ")),
      ].join(","),
    );
  }
  const blob = new Blob(["﻿" + lines.join("\r\n")], {
    type: "text/csv;charset=utf-8",
  });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  URL.revokeObjectURL(a.href);
}

export default function MaterialSummaryTable({
  summary,
  showProjects = false,
}: {
  summary: ProjectSummary;
  showProjects?: boolean;
}) {
  if (summary.rows.length === 0) {
    return (
      <div className="mt-6 text-center text-sm text-zinc-500">
        No approved material rows yet.
        {summary.unreviewed.pending_tables > 0 && (
          <div className="mt-1">
            {summary.unreviewed.pending_tables} table
            {summary.unreviewed.pending_tables === 1 ? "" : "s"} waiting for review.
          </div>
        )}
      </div>
    );
  }
  return (
    <div>
      {(summary.unreviewed.pending_tables > 0 ||
        summary.unreviewed.needs_review_rows > 0) && (
        <div className="mb-3 rounded border border-amber-900 bg-amber-950/50 px-3 py-2 text-xs text-amber-300">
          Not yet counted: {summary.unreviewed.pending_tables} pending table
          {summary.unreviewed.pending_tables === 1 ? "" : "s"},{" "}
          {summary.unreviewed.needs_review_rows} flagged row
          {summary.unreviewed.needs_review_rows === 1 ? "" : "s"}. The totals below
          are partial until those are reviewed.
        </div>
      )}
      <table className="w-full text-sm">
        <thead className="text-left text-xs text-zinc-500">
          <tr>
            <th className="px-2 py-1.5 font-normal">Material</th>
            <th className="px-2 py-1.5 text-right font-normal">Qty</th>
            <th className="px-2 py-1.5 text-right font-normal">Length m</th>
            <th className="px-2 py-1.5 text-right font-normal">Weight kg</th>
            <th className="px-2 py-1.5 font-normal">Cut lengths</th>
            <th className="px-2 py-1.5 font-normal">
              {showProjects ? "Projects" : "Documents"}
            </th>
          </tr>
        </thead>
        <tbody>
          {summary.rows.map((r) => (
            <tr key={r.material_key} className="border-t border-zinc-800/60">
              <td className="px-2 py-1.5">
                <div className="font-medium">{r.material_key}</div>
                {r.description && (
                  <div className="text-xs text-zinc-500">{r.description}</div>
                )}
              </td>
              <td className="px-2 py-1.5 text-right tabular-nums">{r.qty}</td>
              <td className="px-2 py-1.5 text-right tabular-nums">
                {(r.total_length_mm / 1000).toFixed(1)}
              </td>
              <td className="px-2 py-1.5 text-right tabular-nums">
                {r.total_weight_kg.toFixed(1)}
              </td>
              <td className="px-2 py-1.5 text-xs text-zinc-400">
                {r.lengths.map((l) => `${l.qty}×${l.unit_length_mm}`).join(", ") ||
                  "—"}
              </td>
              <td
                className="max-w-48 truncate px-2 py-1.5 text-xs text-zinc-500"
                title={(showProjects ? r.projects : r.documents).join(", ")}
              >
                {(showProjects ? r.projects : r.documents).join(", ")}
              </td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr className="border-t border-zinc-700 font-medium">
            <td className="px-2 py-1.5">Total</td>
            <td className="px-2 py-1.5 text-right tabular-nums">
              {summary.totals.qty}
            </td>
            <td className="px-2 py-1.5 text-right tabular-nums">
              {(summary.totals.total_length_mm / 1000).toFixed(1)}
            </td>
            <td className="px-2 py-1.5 text-right tabular-nums text-emerald-300">
              {summary.totals.total_weight_kg.toFixed(1)}
            </td>
            <td colSpan={2}></td>
          </tr>
        </tfoot>
      </table>
    </div>
  );
}
