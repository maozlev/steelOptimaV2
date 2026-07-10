import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import {
  buildGroups,
  getSummaryIncludes,
  loadHiddenKeys,
  setSummaryIncludes,
} from "../api/bom";
import type { CutoutOut, DocumentOut } from "../api/types";

export default function AggregatedSummaryView({ onBack }: { onBack: () => void }) {
  const [docs, setDocs] = useState<DocumentOut[]>([]);
  const [cutoutsByDoc, setCutoutsByDoc] = useState<Map<number, CutoutOut[]>>(new Map());
  const [loading, setLoading] = useState(true);
  const [included, setIncluded] = useState<Set<number>>(new Set());

  useEffect(() => {
    api.listDocuments().then((allDocs) => {
      setDocs(allDocs);
      const approved = allDocs.filter((d) => d.status === "approved");
      const saved = getSummaryIncludes();
      // First visit: include all approved docs by default
      if (saved === null) {
        const all = new Set(approved.map((d) => d.id));
        setIncluded(all);
        setSummaryIncludes(all);
      } else {
        setIncluded(saved);
      }
      Promise.all(
        approved.map((d) =>
          api.listDocumentCutouts(d.id).then((cs) => [d.id, cs] as const).catch(() => [d.id, []] as const),
        ),
      ).then((pairs) => {
        setCutoutsByDoc(new Map(pairs));
        setLoading(false);
      });
    });
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

  // Aggregate BOM across all included approved docs, respecting per-doc hidden keys
  const aggregated = useMemo(() => {
    type Row = { shape: string; dims: string; qty: number; docNames: string[] };
    const totals = new Map<string, Row>();
    for (const doc of docs) {
      if (doc.status !== "approved" || !included.has(doc.id)) continue;
      const cutouts = cutoutsByDoc.get(doc.id) ?? [];
      const hidden = loadHiddenKeys(doc.id);
      const groups = buildGroups(cutouts).filter(
        (g) => !hidden.has(g.key) && g.active.length > 0,
      );
      for (const g of groups) {
        const existing = totals.get(g.key);
        if (existing) {
          existing.qty += g.active.length;
          existing.docNames.push(doc.filename);
        } else {
          totals.set(g.key, {
            shape: g.shape,
            dims: g.dims,
            qty: g.active.length,
            docNames: [doc.filename],
          });
        }
      }
    }
    return [...totals.values()].sort((a, b) => b.qty - a.qty);
  }, [docs, cutoutsByDoc, included]);

  const totalQty = aggregated.reduce((s, r) => s + r.qty, 0);
  const approvedDocs = docs.filter((d) => d.status === "approved");

  function exportCsv() {
    const lines = ["Shape,Dimensions,Quantity,Documents"];
    for (const row of aggregated) {
      lines.push(`"${row.shape}","${row.dims}",${row.qty},"${[...new Set(row.docNames)].join("; ")}"`);
    }
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
          <p className="text-xs text-zinc-500">Combined bill of materials from approved drawings</p>
        </div>
        <div className="ml-auto flex gap-2">
          <button
            onClick={exportCsv}
            disabled={aggregated.length === 0}
            className="rounded bg-zinc-800 px-3 py-1.5 text-sm hover:bg-zinc-700 disabled:opacity-40"
          >
            Export CSV
          </button>
        </div>
      </header>

      <div className="flex min-h-0 flex-1">
        {/* Sidebar: document selector */}
        <aside className="w-60 flex-shrink-0 overflow-auto border-r border-zinc-800 p-4">
          <h2 className="mb-3 text-xs font-medium uppercase tracking-wide text-zinc-500">
            Approved documents
          </h2>
          {approvedDocs.length === 0 ? (
            <p className="text-xs text-zinc-600">No approved documents yet.</p>
          ) : (
            <ul className="space-y-2">
              {approvedDocs.map((d) => (
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

        {/* Main: aggregated table */}
        <main className="flex-1 overflow-auto p-6">
          {loading ? (
            <p className="text-sm text-zinc-500">Loading cutout data…</p>
          ) : aggregated.length === 0 ? (
            <p className="text-sm text-zinc-500">
              {approvedDocs.length === 0
                ? "No approved documents. Finalize a document from the workspace to include it here."
                : "No visible cutouts — check the document selection or BOM visibility settings."}
            </p>
          ) : (
            <table className="w-full border-collapse text-sm">
              <thead>
                <tr className="border-b border-zinc-700 text-left text-zinc-500">
                  <th className="pb-2 pr-6 font-normal">Shape</th>
                  <th className="pb-2 pr-6 font-normal">Dimensions</th>
                  <th className="pb-2 pr-6 text-right font-normal">Qty</th>
                  <th className="pb-2 font-normal">From</th>
                </tr>
              </thead>
              <tbody>
                {aggregated.map((row) => (
                  <tr
                    key={`${row.shape}|${row.dims}`}
                    className="border-b border-zinc-800/60"
                  >
                    <td className="py-2.5 pr-6 font-medium text-zinc-200">{row.shape}</td>
                    <td className="py-2.5 pr-6 text-zinc-400">{row.dims}</td>
                    <td className="py-2.5 pr-6 text-right tabular-nums font-semibold text-zinc-100">
                      {row.qty}×
                    </td>
                    <td className="py-2.5 text-xs text-zinc-500">
                      {[...new Set(row.docNames)].join(", ")}
                    </td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr className="border-t-2 border-zinc-700">
                  <td colSpan={2} className="pt-3 text-sm font-medium text-zinc-400">
                    Total
                  </td>
                  <td className="pt-3 text-right tabular-nums text-lg font-bold text-zinc-100">
                    {totalQty}×
                  </td>
                  <td />
                </tr>
              </tfoot>
            </table>
          )}
        </main>
      </div>
    </div>
  );
}
