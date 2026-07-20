import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { OrderPlanOut, ProjectSummary, SummaryRow } from "../api/types";
import { netDemand } from "../mockInventory";
import { setViewSection } from "../viewContext";

interface StockDraft {
  length_mm: string;
  price: string;
}

function csvEscape(v: string | number): string {
  const s = String(v);
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

// Order-independent fingerprint of a piece list, so we can tell whether a stored
// plan was optimized against the same quantities the material needs right now.
function piecesSig(pieces: { length_mm: number; qty: number }[]): string {
  return pieces
    .map((p) => `${p.length_mm}:${p.qty}`)
    .sort()
    .join("|");
}

// One CSV for the whole bars order: every material's buy list and per-bar cut
// layout in two labelled sections, a material_key column telling them apart.
function exportOrdersCsv(plans: OrderPlanOut[]) {
  const keyOf = (p: OrderPlanOut) => p.params.material_key ?? "order";
  const lines: string[] = [];

  lines.push("BUY");
  lines.push("material_key,stock_length_mm,bars,unit_price,subtotal");
  let grand = 0;
  for (const p of plans) {
    for (const o of p.result.order) {
      lines.push(
        [csvEscape(keyOf(p)), o.stock_length_mm, o.count, o.unit_price, o.subtotal].join(","),
      );
    }
    grand += p.result.total_cost;
  }
  lines.push(["", "", "", "total", Math.round(grand * 100) / 100].join(","));

  lines.push("");
  lines.push("CUT LAYOUT");
  lines.push("material_key,bar,stock_length_mm,cuts_mm,waste_mm");
  for (const p of plans) {
    p.result.bars.forEach((b, i) => {
      lines.push(
        [csvEscape(keyOf(p)), i + 1, b.stock_length_mm, csvEscape(b.cuts.join(" ")), b.waste_mm].join(
          ",",
        ),
      );
    });
  }

  const blob = new Blob(["﻿" + lines.join("\r\n")], {
    type: "text/csv;charset=utf-8",
  });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "bars_order.csv";
  a.click();
  URL.revokeObjectURL(a.href);
}

function CutStrip({
  stockLength,
  cuts,
  colorFor,
}: {
  stockLength: number;
  cuts: number[];
  colorFor: (length: number) => string;
}) {
  return (
    <div className="flex h-5 w-full overflow-hidden rounded border border-zinc-700 bg-zinc-800">
      {cuts.map((c, i) => (
        <div
          key={i}
          style={{ width: `${(c / stockLength) * 100}%`, backgroundColor: colorFor(c) }}
          className="border-r border-zinc-950 text-center text-[9px] leading-5 text-white/90"
          title={`${c} mm`}
        >
          {c}
        </div>
      ))}
    </div>
  );
}

// One stable colour per distinct piece length across the whole plan, so a given
// length reads the same in every bar. Distinct lengths are spread evenly around
// the hue wheel — no fixed palette to collide when there are many lengths.
function makeColorFor(bars: { cuts: number[] }[]): (length: number) => string {
  const lengths = [...new Set(bars.flatMap((b) => b.cuts))].sort((a, b) => a - b);
  const hue = new Map(
    lengths.map((len, i) => [len, Math.round((i / Math.max(lengths.length, 1)) * 360)]),
  );
  return (len) => `hsl(${hue.get(len) ?? 0}, 60%, 42%)`;
}

export function OrderResult({ shown }: { shown: OrderPlanOut }) {
  const colorFor = makeColorFor(shown.result.bars);
  return (
    <div className="mt-3 border-t border-zinc-800 pt-3">
      <div className="mb-2 flex items-baseline justify-between">
        <span className="text-xs text-zinc-500">
          {(shown.result.total_bought_mm / 1000).toFixed(1)} m bought ·{" "}
          {(shown.result.total_used_mm / 1000).toFixed(1)} m used
        </span>
        <div className="text-sm text-zinc-400">
          waste {shown.result.waste_pct}% ·{" "}
          {(
            (shown.result.total_bought_mm - shown.result.total_used_mm) /
            1000
          ).toFixed(1)}{" "}
          m lost ·{" "}
          <span className="text-lg font-medium text-emerald-300">
            {shown.result.total_cost.toLocaleString()} ₪
          </span>
        </div>
      </div>

      {shown.result.infeasible_lengths_mm.length > 0 && (
        <div className="mb-2 rounded border border-red-800 bg-red-950 px-3 py-2 text-xs text-red-300">
          No stock length can hold pieces of{" "}
          {shown.result.infeasible_lengths_mm.join(", ")} mm — splicing is not
          allowed. Ask the seller for longer bars.
        </div>
      )}

      <table className="mb-3 w-full text-sm">
        <thead className="text-left text-xs text-zinc-500">
          <tr>
            <th className="px-2 py-1 font-normal">Buy</th>
            <th className="px-2 py-1 text-right font-normal">Bars</th>
            <th className="px-2 py-1 text-right font-normal">Unit ₪</th>
            <th className="px-2 py-1 text-right font-normal">Subtotal ₪</th>
          </tr>
        </thead>
        <tbody>
          {shown.result.order.map((o) => (
            <tr key={o.stock_length_mm} className="border-t border-zinc-800/60">
              <td className="px-2 py-1">{o.stock_length_mm} mm</td>
              <td className="px-2 py-1 text-right tabular-nums">{o.count}</td>
              <td className="px-2 py-1 text-right tabular-nums">{o.unit_price}</td>
              <td className="px-2 py-1 text-right tabular-nums">{o.subtotal}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <div className="flex flex-col gap-1">
        {shown.result.bars.map((b, i) => (
          <div key={i} className="flex items-center gap-2">
            <span className="w-20 shrink-0 text-right text-xs tabular-nums text-zinc-500">
              {b.stock_length_mm} mm
            </span>
            <CutStrip
              stockLength={b.stock_length_mm}
              cuts={b.cuts}
              colorFor={colorFor}
            />
            <span className="w-24 shrink-0 text-xs tabular-nums text-zinc-500">
              waste {b.waste_mm} mm
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

function MaterialOrderCard({
  projectId,
  material,
  existingPlan,
  applyInventory,
  onChange,
}: {
  projectId: number;
  material: SummaryRow;
  existingPlan: OrderPlanOut | null;
  applyInventory: boolean;
  onChange: () => void;
}) {
  const nd = applyInventory ? netDemand(material) : null;
  const orderLengths = nd ? nd.netLengths : material.lengths;
  const [stock, setStock] = useState<StockDraft[]>(() =>
    existingPlan
      ? existingPlan.params.stock.map((s) => ({
          length_mm: String(s.length_mm),
          price: String(s.price),
        }))
      : [{ length_mm: "12000", price: "" }],
  );
  const [kerf, setKerf] = useState(
    existingPlan ? String(existingPlan.params.kerf_mm) : "3",
  );
  const [plan, setPlan] = useState<OrderPlanOut | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // A stored plan carries the exact pieces it was optimized against. Compare them
  // to what the material needs *now* (net of inventory when that's on): if they
  // differ, the saved cut layout is stale, so we hide it and re-optimize on the
  // spot — opening Orders always reflects current quantities. Identical pieces →
  // no work, no redundant server round-trip.
  const currentPieces = (nd ? nd.netLengths : material.lengths).map((l) => ({
    length_mm: l.unit_length_mm,
    qty: l.qty,
  }));
  const currentSig = piecesSig(currentPieces);
  const stale =
    existingPlan != null && piecesSig(existingPlan.params.pieces) !== currentSig;
  const shown = plan ?? (stale ? null : existingPlan);

  const autoOptimizedFor = useRef<string | null>(null);
  useEffect(() => {
    if (!stale || busy) return;
    // one recompute per distinct quantity change, guarded so setState from the
    // optimize() call itself can't re-trigger the effect into a loop
    if (autoOptimizedFor.current === currentSig) return;
    autoOptimizedFor.current = currentSig;
    void optimize();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stale, currentSig]);

  async function optimize() {
    setError(null);
    const stockParsed = stock
      .map((s) => ({ length_mm: Number(s.length_mm), price: Number(s.price) }))
      .filter((s) => s.length_mm > 0 && s.price >= 0 && !Number.isNaN(s.price));
    if (stockParsed.length === 0) {
      setError("Add at least one stock length with a price.");
      return;
    }
    // net-of-inventory: send explicit pieces (required − in stock) instead of
    // letting the server pull gross pieces from the summary by material_key
    let pieces: { length_mm: number; qty: number }[] | undefined;
    if (nd) {
      pieces = nd.netLengths.map((l) => ({
        length_mm: l.unit_length_mm,
        qty: l.qty,
      }));
      if (pieces.length === 0) {
        setError("Fully covered by inventory — nothing to order.");
        return;
      }
    }
    setBusy(true);
    try {
      const result = await api.createOrderPlan(projectId, {
        material_key: material.material_key,
        stock: stockParsed,
        kerf_mm: Number(kerf) || 0,
        ...(pieces && { pieces }),
      });
      setPlan(result);
      onChange();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="rounded border border-zinc-800 p-4">
      <div className="mb-3 flex items-baseline justify-between gap-3">
        <h3 className="flex items-center gap-2 font-medium">
          {material.material_key}
          {applyInventory && (
            <span className="rounded bg-emerald-900/60 px-1.5 py-0.5 text-[10px] font-normal text-emerald-300">
              net of stock
            </span>
          )}
        </h3>
        <span className="text-right text-xs text-zinc-500">
          {orderLengths.map((l) => `${l.qty}×${l.unit_length_mm}`).join(", ") ||
            "fully in stock"}
        </span>
      </div>

      <div className="flex flex-wrap items-start gap-4">
        <div>
          <div className="mb-1 text-xs text-zinc-400">
            Seller's stock lengths &amp; prices
          </div>
          {stock.map((s, i) => (
            <div key={i} className="mb-1 flex items-center gap-2">
              <input
                value={s.length_mm}
                onChange={(e) =>
                  setStock((prev) =>
                    prev.map((x, j) =>
                      j === i ? { ...x, length_mm: e.target.value } : x,
                    ),
                  )
                }
                placeholder="length mm"
                className="w-28 rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-sm tabular-nums"
              />
              <span className="text-xs text-zinc-500">mm @</span>
              <input
                value={s.price}
                onChange={(e) =>
                  setStock((prev) =>
                    prev.map((x, j) =>
                      j === i ? { ...x, price: e.target.value } : x,
                    ),
                  )
                }
                placeholder="price"
                className="w-24 rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-sm tabular-nums"
              />
              <span className="text-xs text-zinc-500">₪ / bar</span>
              {stock.length > 1 && (
                <button
                  onClick={() =>
                    setStock((prev) => prev.filter((_, j) => j !== i))
                  }
                  className="rounded px-1.5 text-zinc-500 hover:text-red-400"
                >
                  ✕
                </button>
              )}
            </div>
          ))}
          <button
            onClick={() =>
              setStock((prev) => [...prev, { length_mm: "", price: "" }])
            }
            className="mt-1 rounded bg-zinc-800 px-2 py-1 text-xs hover:bg-zinc-700"
          >
            + stock length
          </button>
        </div>

        <label className="flex flex-col gap-1 text-xs text-zinc-400">
          Kerf mm (lost per cut)
          <input
            value={kerf}
            onChange={(e) => setKerf(e.target.value)}
            className="w-24 rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-sm tabular-nums"
          />
        </label>

        <div className="flex flex-col gap-1">
          <span className="invisible text-xs">.</span>
          <button
            onClick={optimize}
            disabled={busy}
            className="rounded bg-emerald-700 px-4 py-1 text-sm font-medium hover:bg-emerald-600 disabled:opacity-40"
          >
            {busy
              ? "Optimizing…"
              : shown
                ? "Re-optimize order"
                : "Optimize order"}
          </button>
        </div>
      </div>

      {error && (
        <div className="mt-2 rounded border border-red-800 bg-red-950 px-3 py-2 text-sm text-red-300">
          {error}
        </div>
      )}

      {busy && !shown && (
        <div className="mt-2 text-xs text-zinc-500">
          Updating order for current quantities…
        </div>
      )}

      {shown && <OrderResult shown={shown} />}
    </div>
  );
}

export default function OrdersPanel({
  projectId,
  summary,
  applyInventory = false,
}: {
  projectId: number;
  summary: ProjectSummary | null;
  applyInventory?: boolean;
}) {
  const [history, setHistory] = useState<OrderPlanOut[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const seeded = useRef(false);

  const materials = (summary?.rows ?? []).filter((r) => r.lengths.length > 0);

  const refreshHistory = useCallback(
    () =>
      api
        .listOrderPlans(projectId)
        .then((h) => {
          setHistory(h);
          setLoaded(true);
        })
        .catch(() => setLoaded(true)),
    [projectId],
  );
  useEffect(() => {
    refreshHistory();
  }, [refreshHistory]);

  // once history is known, pre-check every material that already has an order
  // so the page opens showing all existing optimizations at once
  useEffect(() => {
    if (!loaded || seeded.current) return;
    seeded.current = true;
    setChecked(
      new Set(
        history
          .map((h) => h.params.material_key)
          .filter((k): k is string => Boolean(k)),
      ),
    );
  }, [loaded, history]);

  function toggle(key: string) {
    setChecked((prev) => {
      const next = new Set(prev);
      next.has(key) ? next.delete(key) : next.add(key);
      return next;
    });
  }

  // tell the assistant dock which order plans are on screen (terse — this
  // rides along with every chat message)
  useEffect(() => {
    const lines = ["order plans:"];
    for (const m of materials) {
      const plan = history.find((h) => h.params.material_key === m.material_key);
      if (!plan) {
        lines.push(`${m.material_key} none`);
        continue;
      }
      const r = plan.result;
      lines.push(
        `${m.material_key} buy ` +
          r.order.map((o) => `${o.count}×${o.stock_length_mm}mm@${o.unit_price}₪`).join("+") +
          ` =${r.total_cost}₪ waste${r.waste_pct}%` +
          (r.infeasible_lengths_mm.length
            ? ` INFEASIBLE:${r.infeasible_lengths_mm.join(",")}mm`
            : ""),
      );
    }
    setViewSection("panel", lines.join("\n"));
    return () => setViewSection("panel", null);
    // materials derives from summary; stringify to avoid re-publishing every render
  }, [history, checked, summary]); // eslint-disable-line react-hooks/exhaustive-deps

  const shownMaterials = materials.filter((m) => checked.has(m.material_key));

  // latest plan per material — the whole bars order, for one combined export
  const latestPlans: OrderPlanOut[] = [];
  const seenKeys = new Set<string>();
  for (const h of history) {
    const k = h.params.material_key;
    if (!k || seenKeys.has(k)) continue;
    seenKeys.add(k);
    latestPlans.push(h);
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="rounded border border-zinc-800 p-4">
        <div className="mb-2 flex items-center justify-between">
          <span className="text-xs text-zinc-400">
            Materials to order (from approved summary)
          </span>
          <button
            onClick={() => exportOrdersCsv(latestPlans)}
            disabled={latestPlans.length === 0}
            className="rounded bg-zinc-800 px-2 py-1 text-xs hover:bg-zinc-700 disabled:opacity-40"
            title={
              latestPlans.length === 0
                ? "No orders to export yet"
                : "Export every material's order as one CSV"
            }
          >
            Export bars order (CSV)
          </button>
        </div>
        {materials.length === 0 ? (
          <div className="text-sm text-zinc-500">
            No approved materials with cut lengths yet.
          </div>
        ) : (
          <div className="flex flex-wrap gap-x-6 gap-y-2">
            {materials.map((m) => {
              const ordered = history.some(
                (h) => h.params.material_key === m.material_key,
              );
              return (
                <label
                  key={m.material_key}
                  className="flex items-center gap-2 text-sm text-zinc-200"
                >
                  <input
                    type="checkbox"
                    checked={checked.has(m.material_key)}
                    onChange={() => toggle(m.material_key)}
                  />
                  {m.material_key}
                  {ordered && (
                    <span className="rounded bg-emerald-900/60 px-1.5 py-0.5 text-[10px] text-emerald-300">
                      ordered
                    </span>
                  )}
                </label>
              );
            })}
          </div>
        )}
      </div>

      {shownMaterials.map((m) => (
        <MaterialOrderCard
          key={m.material_key}
          projectId={projectId}
          material={m}
          existingPlan={
            history.find((h) => h.params.material_key === m.material_key) ?? null
          }
          applyInventory={applyInventory}
          onChange={refreshHistory}
        />
      ))}
    </div>
  );
}
