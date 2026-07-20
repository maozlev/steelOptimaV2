import { CATEGORY_LABEL, materialCategory } from "../materials";
import { MOCK_INVENTORY } from "../mockInventory";

// Read-only view of the (mock) stock table. Bars carry per-length quantities,
// plates a flat quantity. This is the same data netDemand() subtracts from the
// approved summary to produce "to order".
export default function InventoryPanel() {
  const rows = Object.entries(MOCK_INVENTORY)
    .map(([key, entry]) => {
      const byLength = entry.byLength ?? {};
      const lengths = Object.entries(byLength)
        .map(([len, qty]) => ({ len: Number(len), qty }))
        .sort((a, b) => a.len - b.len);
      const totalQty =
        entry.qty ?? lengths.reduce((a, l) => a + l.qty, 0);
      return { key, category: materialCategory(key), lengths, totalQty };
    })
    .sort(
      (a, b) =>
        a.category.localeCompare(b.category) || a.key.localeCompare(b.key),
    );

  return (
    <div className="flex flex-col gap-3">
      <div className="rounded border border-amber-900 bg-amber-950/40 px-3 py-2 text-xs text-amber-300">
        Mock inventory — a stand-in for a real stock table. When you press “Check
        inventory” in Summary, these quantities are subtracted from what the
        tenders require, and the result flows to Bid and Orders.
      </div>

      {rows.length === 0 ? (
        <div className="mt-6 text-center text-sm text-zinc-500">
          Inventory is empty.
        </div>
      ) : (
        <table className="w-full text-sm">
          <thead className="text-left text-xs text-zinc-500">
            <tr>
              <th className="px-2 py-1.5 font-normal">Material</th>
              <th className="px-2 py-1.5 font-normal">Category</th>
              <th className="px-2 py-1.5 font-normal">In stock, by length</th>
              <th className="px-2 py-1.5 text-right font-normal">Total qty</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.key} className="border-t border-zinc-800/60">
                <td className="px-2 py-1.5 font-medium">{r.key}</td>
                <td className="px-2 py-1.5 text-zinc-400">
                  {CATEGORY_LABEL[r.category]}
                </td>
                <td className="px-2 py-1.5 text-xs text-zinc-400">
                  {r.lengths.length > 0
                    ? r.lengths.map((l) => `${l.qty}×${l.len}`).join(", ")
                    : "—"}
                </td>
                <td className="px-2 py-1.5 text-right tabular-nums">
                  {r.totalQty}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
