import { Fragment } from "react";
import type { ProjectSummary, SummaryRow } from "../api/types";
import { CATEGORY_LABEL, CATEGORY_ORDER, materialCategory } from "../materials";
import { netDemand } from "../mockInventory";

function csvEscape(v: string | number | null): string {
  const s = v == null ? "" : String(v);
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

export function exportSummaryCsv(summary: ProjectSummary, filename: string) {
  const header = [
    "category",
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
        CATEGORY_LABEL[materialCategory(r.material_key)],
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

// per-row display numbers, net-of-inventory when applyInventory is on
function rowValues(r: SummaryRow, applyInventory: boolean) {
  if (!applyInventory) {
    return {
      qty: r.qty,
      inStock: 0,
      toOrder: r.qty,
      length_mm: r.total_length_mm,
      weight_kg: r.total_weight_kg,
    };
  }
  const nd = netDemand(r);
  return {
    qty: r.qty,
    inStock: nd.inStockQty,
    toOrder: nd.netQty,
    length_mm: r.total_length_mm * nd.factor,
    weight_kg: r.total_weight_kg * nd.factor,
  };
}

export default function MaterialSummaryTable({
  summary,
  showProjects = false,
  applyInventory = false,
}: {
  summary: ProjectSummary;
  showProjects?: boolean;
  applyInventory?: boolean;
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

  const colSpan = applyInventory ? 8 : 6;
  const grand = summary.rows.reduce(
    (a, r) => {
      const v = rowValues(r, applyInventory);
      return {
        qty: a.qty + v.qty,
        inStock: a.inStock + v.inStock,
        toOrder: a.toOrder + v.toOrder,
        length_mm: a.length_mm + v.length_mm,
        weight_kg: a.weight_kg + v.weight_kg,
      };
    },
    { qty: 0, inStock: 0, toOrder: 0, length_mm: 0, weight_kg: 0 },
  );

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
            {applyInventory && (
              <>
                <th className="px-2 py-1.5 text-right font-normal">In stock</th>
                <th className="px-2 py-1.5 text-right font-normal text-emerald-400">
                  To order
                </th>
              </>
            )}
            <th className="px-2 py-1.5 text-right font-normal">Length m</th>
            <th className="px-2 py-1.5 text-right font-normal">Weight kg</th>
            <th className="px-2 py-1.5 font-normal">Cut lengths</th>
            <th className="px-2 py-1.5 font-normal">
              {showProjects ? "Projects" : "Documents"}
            </th>
          </tr>
        </thead>
        <tbody>
          {CATEGORY_ORDER.map((cat) => {
            const rows = summary.rows.filter(
              (r) => materialCategory(r.material_key) === cat,
            );
            if (rows.length === 0) return null;
            const sub = rows.reduce(
              (a, r) => {
                const v = rowValues(r, applyInventory);
                return {
                  qty: a.qty + v.qty,
                  inStock: a.inStock + v.inStock,
                  toOrder: a.toOrder + v.toOrder,
                  length_mm: a.length_mm + v.length_mm,
                  weight_kg: a.weight_kg + v.weight_kg,
                };
              },
              { qty: 0, inStock: 0, toOrder: 0, length_mm: 0, weight_kg: 0 },
            );
            return (
              <Fragment key={cat}>
                <tr className="bg-zinc-900/40">
                  <td
                    colSpan={colSpan}
                    className="px-2 py-1.5 text-xs font-semibold uppercase tracking-wide text-zinc-400"
                  >
                    {CATEGORY_LABEL[cat]} · {rows.length}
                  </td>
                </tr>
                {rows.map((r: SummaryRow) => {
                  const v = rowValues(r, applyInventory);
                  const covered = applyInventory && v.toOrder === 0;
                  return (
                    <tr
                      key={r.material_key}
                      className={`border-t border-zinc-800/60 ${
                        covered ? "text-zinc-600" : ""
                      }`}
                    >
                      <td className="px-2 py-1.5">
                        <div className="font-medium">{r.material_key}</div>
                        {r.description && (
                          <div className="text-xs text-zinc-500">
                            {r.description}
                          </div>
                        )}
                      </td>
                      <td className="px-2 py-1.5 text-right tabular-nums">
                        {v.qty}
                      </td>
                      {applyInventory && (
                        <>
                          <td className="px-2 py-1.5 text-right tabular-nums text-zinc-500">
                            {v.inStock}
                          </td>
                          <td
                            className={`px-2 py-1.5 text-right font-medium tabular-nums ${
                              covered ? "text-zinc-600" : "text-emerald-300"
                            }`}
                          >
                            {v.toOrder}
                          </td>
                        </>
                      )}
                      <td className="px-2 py-1.5 text-right tabular-nums">
                        {(v.length_mm / 1000).toFixed(1)}
                      </td>
                      <td className="px-2 py-1.5 text-right tabular-nums">
                        {v.weight_kg.toFixed(1)}
                      </td>
                      <td className="px-2 py-1.5 text-xs text-zinc-400">
                        {/* bars list cut lengths; plates have none — show total area */}
                        {r.lengths
                          .map((l) => `${l.qty}×${l.unit_length_mm}`)
                          .join(", ") ||
                          (r.total_area_m2 > 0
                            ? `${r.total_area_m2.toFixed(2)} m²`
                            : "—")}
                      </td>
                      <td
                        className="max-w-64 px-2 py-1.5 text-xs text-zinc-500"
                        title={(showProjects ? r.projects : r.documents).join(", ")}
                      >
                        {/* wrap, don't truncate: a hidden contributor reads as a
                            missing document (e.g. "why isn't synthetic_03 here?"
                            when it's folded into this material by material_key) */}
                        {!showProjects && r.documents.length > 1 && (
                          <span className="mr-1 rounded bg-zinc-800 px-1 py-0.5 text-[10px] text-zinc-400">
                            {r.documents.length} docs
                          </span>
                        )}
                        {(showProjects ? r.projects : r.documents).join(", ")}
                      </td>
                    </tr>
                  );
                })}
                <tr className="border-t border-zinc-800 text-xs text-zinc-400">
                  <td className="px-2 py-1">{CATEGORY_LABEL[cat]} subtotal</td>
                  <td className="px-2 py-1 text-right tabular-nums">{sub.qty}</td>
                  {applyInventory && (
                    <>
                      <td className="px-2 py-1 text-right tabular-nums">
                        {sub.inStock}
                      </td>
                      <td className="px-2 py-1 text-right tabular-nums text-emerald-300">
                        {sub.toOrder}
                      </td>
                    </>
                  )}
                  <td className="px-2 py-1 text-right tabular-nums">
                    {(sub.length_mm / 1000).toFixed(1)}
                  </td>
                  <td className="px-2 py-1 text-right tabular-nums">
                    {sub.weight_kg.toFixed(1)}
                  </td>
                  <td colSpan={2}></td>
                </tr>
              </Fragment>
            );
          })}
        </tbody>
        <tfoot>
          <tr className="border-t border-zinc-700 font-medium">
            <td className="px-2 py-1.5">Total</td>
            <td className="px-2 py-1.5 text-right tabular-nums">{grand.qty}</td>
            {applyInventory && (
              <>
                <td className="px-2 py-1.5 text-right tabular-nums">
                  {grand.inStock}
                </td>
                <td className="px-2 py-1.5 text-right tabular-nums text-emerald-300">
                  {grand.toOrder}
                </td>
              </>
            )}
            <td className="px-2 py-1.5 text-right tabular-nums">
              {(grand.length_mm / 1000).toFixed(1)}
            </td>
            <td className="px-2 py-1.5 text-right tabular-nums text-emerald-300">
              {grand.weight_kg.toFixed(1)}
            </td>
            <td colSpan={2}></td>
          </tr>
        </tfoot>
      </table>
    </div>
  );
}
