import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import { getSummaryIncludes, setSummaryIncludes } from "../api/bom";
import { formatLength } from "../components/BomPanel";
import type { AggregateBom, BomRow } from "../api/types";

/** RFC4180: a field containing a quote escapes it by doubling. The old hand-rolled
 *  writer did not, so a filename with a quote in it corrupted the whole file. */
function csvCell(value: string | number): string {
  const s = String(value);
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

export default function AggregatedSummaryView({ onBack }: { onBack: () => void }) {
  const [bom, setBom] = useState<AggregateBom | null>(null);
  const [loading, setLoading] = useState(true);
  const [included, setIncluded] = useState<Set<number>>(new Set());

  useEffect(() => {
    api
      .getAggregateBom()
      .then((b) => {
        setBom(b);
        const saved = getSummaryIncludes();
        // First visit: include every approved document.
        const next = saved ?? new Set(b.documents.map((d) => d.id));
        if (saved === null) setSummaryIncludes(next);
        setIncluded(next);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  function toggleInclude(docId: number) {
    setIncluded((prev) => {
      const next = new Set(prev);
      if (next.has(docId)) next.delete(docId);
      else next.add(docId);
      setSummaryIncludes(next);
      return next;
    });
  }

  const docs = bom?.documents ?? [];
  const includedNames = useMemo(
    () => new Set(docs.filter((d) => included.has(d.id)).map((d) => d.filename)),
    [docs, included],
  );

  // The server rolls up every approved document; the checkboxes then narrow that
  // to the ones you want in this work order.
  const rows: BomRow[] = useMemo(
    () =>
      (bom?.rows ?? []).filter((r) =>
        (r.documents ?? []).some((n) => includedNames.has(n)),
      ),
    [bom, includedNames],
  );

  const totalQty = rows.reduce((s, r) => s + r.qty, 0);
  const totalCut = rows.reduce((s, r) => s + r.cut_length_total_mm, 0);

  function exportCsv() {
    const lines = [
      ["Shape", "Dimensions", "Quantity", "Cut length each (mm)", "Cut length total (mm)", "Documents"]
        .map(csvCell)
        .join(","),
    ];
    for (const r of rows) {
      lines.push(
        [
          r.shape_label,
          r.dims,
          r.qty,
          r.cut_length_each_mm,
          r.cut_length_total_mm,
          [...new Set(r.documents ?? [])].join("; "),
        ]
          .map(csvCell)
          .join(","),
      );
    }
    lines.push(["TOTAL", "", totalQty, "", totalCut.toFixed(2), ""].map(csvCell).join(","));
    const blob = new Blob([lines.join("\n")], { type: "text/csv" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "bom_summary.csv";
    a.click();
    URL.revokeObjectURL(a.href);
  }

  return (
    <div className="flex h-full flex-col bg-zinc-950">
      <header className="flex items-center gap-4 border-b border-zinc-800 px-6 py-3">
        <button
          onClick={onBack}
          className="rounded px-2 py-1 text-sm text-zinc-400 hover:bg-zinc-900 hover:text-zinc-200"
        >
          ← Documents
        </button>
        <div>
          <h1 className="text-lg font-semibold">Aggregated BOM Summary</h1>
          <p className="text-xs text-zinc-500">
            Combined bill of materials from approved drawings
          </p>
        </div>
        <div className="ml-auto flex gap-2">
          <button
            onClick={exportCsv}
            disabled={rows.length === 0}
            className="rounded bg-zinc-800 px-3 py-1.5 text-sm hover:bg-zinc-700 disabled:opacity-40"
          >
            Export CSV
          </button>
        </div>
      </header>

      <div className="flex min-h-0 flex-1">
        <aside className="w-60 flex-shrink-0 overflow-auto border-r border-zinc-800 p-4">
          <h2 className="mb-3 text-xs font-medium uppercase tracking-wide text-zinc-500">
            Approved documents
          </h2>
          {docs.length === 0 ? (
            <p className="text-xs text-zinc-600">No approved documents yet.</p>
          ) : (
            <ul className="space-y-2">
              {docs.map((d) => (
                <li key={d.id} className="flex items-start gap-2">
                  <input
                    type="checkbox"
                    id={`doc-${d.id}`}
                    checked={included.has(d.id)}
                    onChange={() => toggleInclude(d.id)}
                    className="mt-0.5 accent-emerald-500"
                  />
                  <label
                    htmlFor={`doc-${d.id}`}
                    className={`cursor-pointer text-xs leading-snug ${
                      included.has(d.id) ? "text-zinc-200" : "text-zinc-600"
                    }`}
                  >
                    {d.filename}
                  </label>
                </li>
              ))}
            </ul>
          )}
        </aside>

        <main className="flex-1 overflow-auto p-6">
          {loading ? (
            <p className="text-sm text-zinc-500">Loading cutout data…</p>
          ) : rows.length === 0 ? (
            <p className="text-sm text-zinc-500">
              {docs.length === 0
                ? "No approved documents. Finalize a document from the workspace to include it here."
                : "No cutouts — check the document selection."}
            </p>
          ) : (
            <table className="w-full border-collapse text-sm">
              <thead>
                <tr className="border-b border-zinc-700 text-left text-zinc-500">
                  <th className="pb-2 pr-6 font-normal">Shape</th>
                  <th className="pb-2 pr-6 font-normal">Dimensions</th>
                  <th className="pb-2 pr-6 text-right font-normal">Qty</th>
                  <th className="pb-2 pr-6 text-right font-normal">Cut ea.</th>
                  <th className="pb-2 pr-6 text-right font-normal">Cut total</th>
                  <th className="pb-2 font-normal">From</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.key} className="border-b border-zinc-800/60">
                    <td className="py-2.5 pr-6 font-medium text-zinc-200">
                      {row.shape_label}
                    </td>
                    <td className="py-2.5 pr-6 text-zinc-400">{row.dims}</td>
                    <td className="py-2.5 pr-6 text-right font-semibold tabular-nums text-zinc-100">
                      {row.qty}×
                    </td>
                    <td className="py-2.5 pr-6 text-right tabular-nums text-zinc-500">
                      {formatLength(row.cut_length_each_mm)}
                    </td>
                    <td className="py-2.5 pr-6 text-right tabular-nums text-zinc-300">
                      {formatLength(row.cut_length_total_mm)}
                    </td>
                    <td className="py-2.5 text-xs text-zinc-500">
                      {[...new Set(row.documents ?? [])].join(", ")}
                    </td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr className="border-t-2 border-zinc-700">
                  <td colSpan={2} className="pt-3 text-sm font-medium text-zinc-400">
                    Total
                  </td>
                  <td className="pt-3 pr-6 text-right text-lg font-bold tabular-nums text-zinc-100">
                    {totalQty}×
                  </td>
                  <td />
                  <td className="pt-3 pr-6 text-right text-lg font-bold tabular-nums text-emerald-300">
                    {formatLength(totalCut)}
                  </td>
                  <td className="pt-3 text-xs text-zinc-500">total cut length</td>
                </tr>
              </tfoot>
            </table>
          )}
        </main>
      </div>
    </div>
  );
}
