import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import type { OrderPlanOut, ProjectSummary } from "../api/types";

interface StockDraft {
  length_mm: string;
  price: string;
}

function CutStrip({
  stockLength,
  cuts,
}: {
  stockLength: number;
  cuts: number[];
}) {
  const palette = [
    "bg-emerald-700",
    "bg-sky-700",
    "bg-violet-700",
    "bg-rose-700",
    "bg-amber-700",
  ];
  return (
    <div className="flex h-5 w-full overflow-hidden rounded border border-zinc-700 bg-zinc-800">
      {cuts.map((c, i) => (
        <div
          key={i}
          style={{ width: `${(c / stockLength) * 100}%` }}
          className={`${palette[i % palette.length]} border-r border-zinc-950 text-center text-[9px] leading-5 text-white/80`}
          title={`${c} mm`}
        >
          {c}
        </div>
      ))}
    </div>
  );
}

export default function OrdersPanel({
  projectId,
  summary,
}: {
  projectId: number;
  summary: ProjectSummary | null;
}) {
  const [materialKey, setMaterialKey] = useState<string>("");
  const [stock, setStock] = useState<StockDraft[]>([{ length_mm: "12000", price: "" }]);
  const [kerf, setKerf] = useState("3");
  const [plan, setPlan] = useState<OrderPlanOut | null>(null);
  const [history, setHistory] = useState<OrderPlanOut[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const materials = (summary?.rows ?? []).filter((r) => r.lengths.length > 0);

  const refreshHistory = useCallback(
    () => api.listOrderPlans(projectId).then(setHistory).catch(() => {}),
    [projectId],
  );
  useEffect(() => {
    refreshHistory();
  }, [refreshHistory]);

  async function optimize() {
    setError(null);
    const stockParsed = stock
      .map((s) => ({ length_mm: Number(s.length_mm), price: Number(s.price) }))
      .filter((s) => s.length_mm > 0 && s.price >= 0 && !Number.isNaN(s.price));
    if (!materialKey || stockParsed.length === 0) {
      setError("Pick a material and at least one stock length with a price.");
      return;
    }
    setBusy(true);
    try {
      const result = await api.createOrderPlan(projectId, {
        material_key: materialKey,
        stock: stockParsed,
        kerf_mm: Number(kerf) || 0,
      });
      setPlan(result);
      refreshHistory();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const shown = plan ?? history[0] ?? null;

  return (
    <div className="flex flex-col gap-4">
      <div className="rounded border border-zinc-800 p-4">
        <div className="flex flex-wrap items-end gap-4">
          <label className="flex flex-col gap-1 text-xs text-zinc-400">
            Material (from approved summary)
            <select
              value={materialKey}
              onChange={(e) => setMaterialKey(e.target.value)}
              className="rounded border border-zinc-700 bg-zinc-900 px-2 py-1.5 text-sm text-zinc-100"
            >
              <option value="">choose…</option>
              {materials.map((m) => (
                <option key={m.material_key} value={m.material_key}>
                  {m.material_key} (
                  {m.lengths.map((l) => `${l.qty}×${l.unit_length_mm}`).join(", ")})
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs text-zinc-400">
            Kerf mm (lost per cut)
            <input
              value={kerf}
              onChange={(e) => setKerf(e.target.value)}
              className="w-24 rounded border border-zinc-700 bg-zinc-900 px-2 py-1.5 text-sm"
            />
          </label>
        </div>

        <div className="mt-3">
          <div className="mb-1 text-xs text-zinc-400">
            Seller's stock lengths &amp; prices
          </div>
          {stock.map((s, i) => (
            <div key={i} className="mb-1 flex items-center gap-2">
              <input
                value={s.length_mm}
                onChange={(e) =>
                  setStock((prev) =>
                    prev.map((x, j) => (j === i ? { ...x, length_mm: e.target.value } : x)),
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
                    prev.map((x, j) => (j === i ? { ...x, price: e.target.value } : x)),
                  )
                }
                placeholder="price"
                className="w-24 rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-sm tabular-nums"
              />
              <span className="text-xs text-zinc-500">₪ / bar</span>
              {stock.length > 1 && (
                <button
                  onClick={() => setStock((prev) => prev.filter((_, j) => j !== i))}
                  className="rounded px-1.5 text-zinc-500 hover:text-red-400"
                >
                  ✕
                </button>
              )}
            </div>
          ))}
          <button
            onClick={() => setStock((prev) => [...prev, { length_mm: "", price: "" }])}
            className="mt-1 rounded bg-zinc-800 px-2 py-1 text-xs hover:bg-zinc-700"
          >
            + stock length
          </button>
        </div>

        <button
          onClick={optimize}
          disabled={busy}
          className="mt-4 rounded bg-emerald-700 px-4 py-2 text-sm font-medium hover:bg-emerald-600 disabled:opacity-40"
        >
          {busy ? "Optimizing…" : "Optimize order"}
        </button>
        {error && (
          <div className="mt-2 rounded border border-red-800 bg-red-950 px-3 py-2 text-sm text-red-300">
            {error}
          </div>
        )}
      </div>

      {shown && (
        <div className="rounded border border-zinc-800 p-4">
          <div className="mb-2 flex items-baseline justify-between">
            <h3 className="font-medium">
              Order{shown.params.material_key ? ` — ${shown.params.material_key}` : ""}
            </h3>
            <div className="text-sm text-zinc-400">
              waste {shown.result.waste_pct}% ·{" "}
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
                <CutStrip stockLength={b.stock_length_mm} cuts={b.cuts} />
                <span className="w-24 shrink-0 text-xs tabular-nums text-zinc-500">
                  waste {b.waste_mm}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
