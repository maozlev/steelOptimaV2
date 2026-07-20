import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import type { BidOut, PriceEntry, PricingUnit } from "../api/types";
import { netDemand } from "../mockInventory";
import { setViewSection } from "../viewContext";

const UNIT_LABEL: Record<PricingUnit, string> = {
  per_kg: "₪ / kg",
  per_m: "₪ / m",
  per_unit: "₪ / unit",
};

export default function BidPanel({
  projectId,
  applyInventory = false,
}: {
  projectId: number;
  applyInventory?: boolean;
}) {
  const [bid, setBid] = useState<BidOut | null>(null);
  const [drafts, setDrafts] = useState<Map<string, PriceEntry>>(new Map());
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  // The unit a freshly-priced line inherits, and what the top-of-panel dropdown
  // stamps onto every line at once — so the operator sets "price by kg" once
  // instead of clicking the per-line select on every row.
  const [defaultUnit, setDefaultUnit] = useState<PricingUnit>("per_kg");

  const refresh = useCallback(
    () => api.getBid(projectId).then(setBid).catch((e) => setError(e.message)),
    [projectId],
  );
  useEffect(() => {
    refresh();
  }, [refresh]);

  // tell the assistant dock what the bid table shows right now (terse — this
  // rides along with every chat message)
  useEffect(() => {
    if (!bid) return;
    const lines = [`bid(${applyInventory ? "net" : "gross"}):`];
    let total = 0;
    for (const r of bid.rows) {
      const factor = applyInventory ? netDemand(r).factor : 1;
      const lineTotal = r.line_total != null ? r.line_total * factor : null;
      if (lineTotal != null) total += lineTotal;
      lines.push(
        `${r.material_key} qty${applyInventory ? netDemand(r).netQty : r.qty} ` +
          (r.price != null
            ? `@${r.price}${(r.pricing_unit ?? "").replace("per_", "/")} =${lineTotal != null ? Math.round(lineTotal) : "?"}₪`
            : "UNPRICED"),
      );
    }
    lines.push(`total ${Math.round(total)}₪ (priced lines only)`);
    setViewSection("panel", lines.join("\n"));
    return () => setViewSection("panel", null);
  }, [bid, applyInventory]);

  function draftFor(key: string): PriceEntry | undefined {
    return drafts.get(key);
  }

  function setDraft(key: string, patch: Partial<PriceEntry>) {
    setDrafts((prev) => {
      const row = bid?.rows.find((r) => r.material_key === key);
      const current: PriceEntry = prev.get(key) ?? {
        material_key: key,
        price: row?.price ?? 0,
        pricing_unit: row?.pricing_unit ?? defaultUnit,
      };
      return new Map(prev).set(key, { ...current, ...patch });
    });
  }

  // Stamp one unit onto every line that already carries a price (saved or drafted),
  // and make it the default for lines priced later. Lines still without a price are
  // left alone — we don't want to silently turn them into priced-at-0 rows.
  function applyUnitToAll(u: PricingUnit) {
    setDefaultUnit(u);
    setDrafts((prev) => {
      const next = new Map(prev);
      for (const row of bid?.rows ?? []) {
        const existing = next.get(row.material_key);
        const hasPrice = existing ? existing.price > 0 : row.price != null;
        if (!hasPrice) continue;
        const current: PriceEntry = existing ?? {
          material_key: row.material_key,
          price: row.price ?? 0,
          pricing_unit: row.pricing_unit ?? u,
        };
        next.set(row.material_key, { ...current, pricing_unit: u });
      }
      return next;
    });
  }

  async function saveAll() {
    if (drafts.size === 0) return;
    setSaving(true);
    setError(null);
    try {
      await api.putPrices(projectId, [...drafts.values()]);
      setDrafts(new Map());
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  if (!bid) {
    return <div className="text-sm text-zinc-500">{error ?? "Loading bid…"}</div>;
  }
  if (bid.rows.length === 0) {
    return (
      <div className="mt-6 text-center text-sm text-zinc-500">
        Nothing to price — approve material tables first.
      </div>
    );
  }

  // quantities/prices are all linear in qty, so net-of-inventory scales each
  // line and the bid total by factor = netQty / grossQty
  const netTotal = applyInventory
    ? bid.rows.reduce(
        (a, r) =>
          a + (r.line_total != null ? r.line_total * netDemand(r).factor : 0),
        0,
      )
    : bid.total;

  return (
    <div className="flex flex-col gap-3">
      {error && (
        <div className="rounded border border-red-800 bg-red-950 px-3 py-2 text-sm text-red-300">
          {error}
        </div>
      )}
      {applyInventory && (
        <div className="rounded border border-emerald-900 bg-emerald-950/40 px-3 py-2 text-xs text-emerald-300">
          Quantities, weights and line totals are net of inventory — what this bid
          actually covers to order.
        </div>
      )}
      {bid.unpriced_keys.length > 0 && (
        <div className="rounded border border-amber-900 bg-amber-950/50 px-3 py-2 text-xs text-amber-300">
          {bid.unpriced_keys.length} material
          {bid.unpriced_keys.length === 1 ? "" : "s"} without a price — they are NOT
          in the total.
        </div>
      )}
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="text-zinc-400">Price all lines by</span>
        <select
          value={defaultUnit}
          onChange={(e) => applyUnitToAll(e.target.value as PricingUnit)}
          className="rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-sm"
        >
          {(Object.keys(UNIT_LABEL) as PricingUnit[]).map((u) => (
            <option key={u} value={u}>
              {UNIT_LABEL[u]}
            </option>
          ))}
        </select>
        <span className="text-xs text-zinc-500">
          applies to every priced line — override a single line below if needed
        </span>
      </div>
      <table className="w-full text-sm">
        <thead className="text-left text-xs text-zinc-500">
          <tr>
            <th className="px-2 py-1.5 font-normal">Material</th>
            <th className="px-2 py-1.5 text-right font-normal">Qty</th>
            <th className="px-2 py-1.5 text-right font-normal">Length m</th>
            <th className="px-2 py-1.5 text-right font-normal">Weight kg</th>
            <th className="px-2 py-1.5 font-normal">Price</th>
            <th className="px-2 py-1.5 font-normal">Unit</th>
            <th className="px-2 py-1.5 text-right font-normal">Line total ₪</th>
          </tr>
        </thead>
        <tbody>
          {bid.rows.map((r) => {
            const draft = draftFor(r.material_key);
            const unpriced = r.line_total == null && !draft;
            const factor = applyInventory ? netDemand(r).factor : 1;
            const qty = applyInventory ? netDemand(r).netQty : r.qty;
            const lineTotal =
              r.line_total != null ? r.line_total * factor : null;
            return (
              <tr
                key={r.material_key}
                className={`border-t border-zinc-800/60 ${
                  unpriced ? "bg-amber-950/20" : ""
                } ${applyInventory && qty === 0 ? "text-zinc-600" : ""}`}
              >
                <td className="px-2 py-1.5">
                  <div className="font-medium">{r.material_key}</div>
                  {r.description && (
                    <div className="text-xs text-zinc-500">{r.description}</div>
                  )}
                </td>
                <td className="px-2 py-1.5 text-right tabular-nums">{qty}</td>
                <td className="px-2 py-1.5 text-right tabular-nums">
                  {((r.total_length_mm / 1000) * factor).toFixed(1)}
                </td>
                <td className="px-2 py-1.5 text-right tabular-nums">
                  {(r.total_weight_kg * factor).toFixed(1)}
                </td>
                <td className="px-2 py-1.5">
                  <input
                    type="number"
                    min={0}
                    step="0.01"
                    value={draft?.price ?? r.price ?? ""}
                    onChange={(e) =>
                      setDraft(r.material_key, { price: Number(e.target.value) })
                    }
                    className="w-24 rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-right text-sm tabular-nums"
                  />
                </td>
                <td className="px-2 py-1.5">
                  <select
                    // fall back to the global unit, not "" — so the top-of-panel
                    // dropdown visibly sets the unit on every line (even unpriced
                    // ones) without a draft that would fake a price of 0
                    value={draft?.pricing_unit ?? r.pricing_unit ?? defaultUnit}
                    onChange={(e) =>
                      setDraft(r.material_key, {
                        pricing_unit: e.target.value as PricingUnit,
                      })
                    }
                    className="rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-sm"
                  >
                    {(Object.keys(UNIT_LABEL) as PricingUnit[]).map((u) => (
                      <option key={u} value={u}>
                        {UNIT_LABEL[u]}
                      </option>
                    ))}
                  </select>
                </td>
                <td className="px-2 py-1.5 text-right tabular-nums">
                  {lineTotal != null
                    ? Math.round(lineTotal).toLocaleString()
                    : "—"}
                </td>
              </tr>
            );
          })}
        </tbody>
        <tfoot>
          <tr className="border-t border-zinc-700 font-medium">
            <td className="px-2 py-2" colSpan={6}>
              Bid total (priced lines only)
            </td>
            <td className="px-2 py-2 text-right text-lg tabular-nums text-emerald-300">
              {Math.round(netTotal).toLocaleString()} ₪
            </td>
          </tr>
        </tfoot>
      </table>
      <div>
        <button
          onClick={saveAll}
          disabled={drafts.size === 0 || saving}
          className="rounded bg-emerald-700 px-4 py-2 text-sm font-medium hover:bg-emerald-600 disabled:opacity-40"
        >
          {saving ? "Saving…" : `Save prices${drafts.size ? ` (${drafts.size})` : ""}`}
        </button>
      </div>
    </div>
  );
}
