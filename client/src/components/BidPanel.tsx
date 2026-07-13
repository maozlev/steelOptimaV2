import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import type { BidOut, PriceEntry, PricingUnit } from "../api/types";

const UNIT_LABEL: Record<PricingUnit, string> = {
  per_kg: "₪ / kg",
  per_m: "₪ / m",
  per_unit: "₪ / unit",
};

export default function BidPanel({ projectId }: { projectId: number }) {
  const [bid, setBid] = useState<BidOut | null>(null);
  const [drafts, setDrafts] = useState<Map<string, PriceEntry>>(new Map());
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const refresh = useCallback(
    () => api.getBid(projectId).then(setBid).catch((e) => setError(e.message)),
    [projectId],
  );
  useEffect(() => {
    refresh();
  }, [refresh]);

  function draftFor(key: string): PriceEntry | undefined {
    return drafts.get(key);
  }

  function setDraft(key: string, patch: Partial<PriceEntry>) {
    setDrafts((prev) => {
      const row = bid?.rows.find((r) => r.material_key === key);
      const current: PriceEntry = prev.get(key) ?? {
        material_key: key,
        price: row?.price ?? 0,
        pricing_unit: row?.pricing_unit ?? "per_kg",
      };
      return new Map(prev).set(key, { ...current, ...patch });
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

  return (
    <div className="flex flex-col gap-3">
      {error && (
        <div className="rounded border border-red-800 bg-red-950 px-3 py-2 text-sm text-red-300">
          {error}
        </div>
      )}
      {bid.unpriced_keys.length > 0 && (
        <div className="rounded border border-amber-900 bg-amber-950/50 px-3 py-2 text-xs text-amber-300">
          {bid.unpriced_keys.length} material
          {bid.unpriced_keys.length === 1 ? "" : "s"} without a price — they are NOT
          in the total.
        </div>
      )}
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
            return (
              <tr
                key={r.material_key}
                className={`border-t border-zinc-800/60 ${
                  unpriced ? "bg-amber-950/20" : ""
                }`}
              >
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
                    value={draft?.pricing_unit ?? r.pricing_unit ?? ""}
                    onChange={(e) =>
                      setDraft(r.material_key, {
                        pricing_unit: e.target.value as PricingUnit,
                      })
                    }
                    className="rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-sm"
                  >
                    <option value="" disabled>
                      unit…
                    </option>
                    {(Object.keys(UNIT_LABEL) as PricingUnit[]).map((u) => (
                      <option key={u} value={u}>
                        {UNIT_LABEL[u]}
                      </option>
                    ))}
                  </select>
                </td>
                <td className="px-2 py-1.5 text-right tabular-nums">
                  {r.line_total != null ? r.line_total.toLocaleString() : "—"}
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
              {bid.total.toLocaleString()} ₪
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
