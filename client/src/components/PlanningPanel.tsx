import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import type { OrderPlanOut } from "../api/types";
import { MOCK_INVENTORY } from "../mockInventory";
import {
  allocate,
  clearPlan,
  emptyPlan,
  isPlate,
  loadPlan,
  plateVolumeM3,
  plateWeightKg,
  savePlan,
  type PlanItem,
  type PlanState,
  type PlateUnit,
  type StockLine,
} from "../planning";
import { readViewContext, setViewSection } from "../viewContext";
import ChatPanel from "./ChatPanel";
import { OrderResult } from "./OrdersPanel";

// The action menu for the planning conversation — replaces the global one.
const PLAN_TOOLS = `[[TOOLS]]
You are planning a build WITH the user, in two stages. Stage "draft": build the parts list. Stage "approved": price the missing parts and optimize the buy. The context lists warehouse stock, plan items (in stock / missing), stage, seller catalogs, plate prices and optimization results — answer availability questions from it. To change the plan when asked, end your answer with blocks like:
[[ACTION]]{"type":"plan_add_item","material_key":"L50X50X5","unit_length_mm":2000,"qty":8}[[/ACTION]]
Types: plan_add_item{material_key,qty,unit_length_mm for bars | thk_mm,w_mm,h_mm for plates} · plan_set_qty{material_key,qty,unit_length_mm?} · plan_remove_item{material_key,unit_length_mm?} · plan_approve{} (lock the list, move to buying) · plan_reopen{} (back to editing) · plan_set_stock{material_key,lengths:[{length_mm,price}]} (seller bar catalog, ₪/bar) · plan_set_plate_price{material_key,price,unit:per_kg|per_m3} · plan_set_kerf{kerf_mm} · plan_optimize{} (approved stage only)
Item edits work only in draft; optimize only after approval. Strict JSON inside blocks. Never act unasked.
[[/TOOLS]]`;

const inputCls =
  "rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-sm tabular-nums";

export default function PlanningPanel({
  projectId,
  onGlobalAction,
}: {
  projectId: number;
  /** Fallback for non-planning actions (set_inventory etc.). */
  onGlobalAction?: (a: Record<string, unknown>) => Promise<string>;
}) {
  const [plan, setPlan] = useState<PlanState>(() => loadPlan(projectId));
  const [results, setResults] = useState<Record<string, OrderPlanOut>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState({ material: "", length: "", qty: "" });

  const editing = plan.stage === "draft";

  // survives refresh (Maoz's ask) — results stay derived and are re-run on demand
  useEffect(() => savePlan(projectId, plan), [projectId, plan]);

  const alloc = useMemo(() => allocate(plan.items), [plan.items]);

  /* ---------- mutations (used by both the board UI and the agent) ---------- */

  function addItem(
    material_key: string,
    qty: number,
    unit_length_mm?: number,
    dims?: { thk_mm?: number; w_mm?: number; h_mm?: number },
  ) {
    setPlan((p) => ({
      ...p,
      nextId: p.nextId + 1,
      items: [
        ...p.items,
        {
          id: p.nextId,
          material_key: material_key.trim().toUpperCase(),
          qty,
          unit_length_mm,
          ...dims,
        },
      ],
    }));
  }

  const matches = (it: PlanItem, key: string, len?: number) =>
    it.material_key === key.trim().toUpperCase() &&
    (len == null || it.unit_length_mm === len);

  function setStockCatalog(key: string, lines: StockLine[]) {
    setPlan((p) => ({
      ...p,
      stock: { ...p.stock, [key.trim().toUpperCase()]: lines },
    }));
  }

  function setPlatePrice(key: string, price: number, unit: PlateUnit) {
    setPlan((p) => ({
      ...p,
      platePrice: { ...p.platePrice, [key.trim().toUpperCase()]: { price, unit } },
    }));
  }

  /* ---------- optimization ---------- */

  const optimize = useCallback(async (): Promise<string> => {
    setError(null);
    setBusy(true);
    try {
      // group missing bar pieces per material
      const allocNow = allocate(plan.items);
      const byMat = new Map<string, Map<number, number>>();
      plan.items.forEach((it, idx) => {
        if (isPlate(it) || it.unit_length_mm == null) return;
        const miss = allocNow[idx].missing;
        if (miss <= 0) return;
        const m = byMat.get(it.material_key) ?? new Map<number, number>();
        m.set(it.unit_length_mm, (m.get(it.unit_length_mm) ?? 0) + miss);
        byMat.set(it.material_key, m);
      });
      const next: Record<string, OrderPlanOut> = {};
      const skipped: string[] = [];
      for (const [key, lens] of byMat) {
        const stock = (plan.stock[key] ?? []).filter(
          (s) => s.length_mm > 0 && s.price >= 0,
        );
        if (stock.length === 0) {
          skipped.push(key);
          continue;
        }
        // no material_key on purpose: planning plans must not shadow the
        // tender orders shown in the Orders tab (it keys on material_key)
        next[key] = await api.createOrderPlan(projectId, {
          pieces: [...lens].map(([length_mm, qty]) => ({ length_mm, qty })),
          stock,
          kerf_mm: plan.kerf_mm,
        });
      }
      setResults(next);
      const done = Object.keys(next).length;
      return (
        `optimized ${done} material${done === 1 ? "" : "s"}` +
        (skipped.length ? `; no seller catalog for: ${skipped.join(", ")}` : "")
      );
    } finally {
      setBusy(false);
    }
  }, [plan, projectId]);

  /* ---------- agent dispatcher ---------- */

  const onAction = useCallback(
    async (a: Record<string, unknown>): Promise<string> => {
      const key = String(a.material_key ?? "").trim().toUpperCase();
      const len = a.unit_length_mm != null ? Number(a.unit_length_mm) : undefined;
      const needDraft = () => {
        if (!editing)
          throw new Error("plan is approved — plan_reopen first to edit items");
      };
      switch (a.type) {
        case "plan_add_item": {
          needDraft();
          const qty = Number(a.qty);
          if (!key || Number.isNaN(qty) || qty <= 0)
            throw new Error("plan_add_item needs material_key and qty > 0");
          const dims =
            a.thk_mm != null
              ? {
                  thk_mm: Number(a.thk_mm),
                  w_mm: Number(a.w_mm),
                  h_mm: Number(a.h_mm),
                }
              : undefined;
          if (!dims && len == null && !/^PLATE-/i.test(key))
            throw new Error("a bar item needs unit_length_mm");
          addItem(key, qty, len, dims);
          return `added ${qty}× ${key}${len ? ` @${len}mm` : ""}`;
        }
        case "plan_set_qty": {
          needDraft();
          const qty = Number(a.qty);
          if (Number.isNaN(qty) || qty <= 0)
            throw new Error("plan_set_qty needs qty > 0 (use plan_remove_item to delete)");
          const hit = plan.items.filter((it) => matches(it, key, len));
          if (hit.length === 0) throw new Error(`no plan item matches ${key}`);
          if (hit.length > 1 && len == null)
            throw new Error(`${key} appears at several lengths — say which unit_length_mm`);
          setPlan((p) => ({
            ...p,
            items: p.items.map((it) => (it.id === hit[0].id ? { ...it, qty } : it)),
          }));
          return `${key}${len ? ` @${len}mm` : ""} qty → ${qty}`;
        }
        case "plan_remove_item": {
          needDraft();
          const hit = plan.items.filter((it) => matches(it, key, len));
          if (hit.length === 0) throw new Error(`no plan item matches ${key}`);
          const ids = new Set(hit.map((h) => h.id));
          setPlan((p) => ({ ...p, items: p.items.filter((it) => !ids.has(it.id)) }));
          return `removed ${hit.length} item${hit.length === 1 ? "" : "s"} of ${key}`;
        }
        case "plan_approve":
          if (plan.items.length === 0) throw new Error("nothing to approve — the plan is empty");
          setPlan((p) => ({ ...p, stage: "approved" }));
          return "plan approved — on to pricing & optimization";
        case "plan_reopen":
          setPlan((p) => ({ ...p, stage: "draft" }));
          return "plan reopened for editing";
        case "plan_set_stock": {
          const lines = (Array.isArray(a.lengths) ? a.lengths : []).map((s) => ({
            length_mm: Number((s as Record<string, unknown>).length_mm),
            price: Number((s as Record<string, unknown>).price),
          }));
          if (!key || lines.length === 0 || lines.some((l) => !(l.length_mm > 0)))
            throw new Error("plan_set_stock needs material_key and lengths:[{length_mm,price}]");
          setStockCatalog(key, lines);
          return `seller catalog for ${key}: ${lines
            .map((l) => `${l.length_mm}mm@${l.price}₪`)
            .join(", ")}`;
        }
        case "plan_set_plate_price": {
          const price = Number(a.price);
          const unit = a.unit as PlateUnit;
          if (!key || Number.isNaN(price) || !["per_kg", "per_m3"].includes(unit))
            throw new Error("plan_set_plate_price needs material_key, price, unit per_kg|per_m3");
          setPlatePrice(key, price, unit);
          return `${key} priced ${price}₪ ${unit === "per_kg" ? "/kg" : "/m³"}`;
        }
        case "plan_set_kerf": {
          const kerf = Number(a.kerf_mm);
          if (Number.isNaN(kerf) || kerf < 0) throw new Error("kerf_mm must be ≥ 0");
          setPlan((p) => ({ ...p, kerf_mm: kerf }));
          return `kerf → ${kerf}mm`;
        }
        case "plan_optimize":
          if (editing)
            throw new Error("approve the plan first (plan_approve), then optimize");
          return optimize();
        default:
          if (onGlobalAction) return onGlobalAction(a);
          throw new Error(`unknown action type: ${String(a.type)}`);
      }
    },
    [plan, editing, optimize, onGlobalAction],
  );

  /* ---------- what the agent sees ---------- */

  useEffect(() => {
    const lines = [`planning board (stage: ${plan.stage}):`];
    lines.push(
      "warehouse: " +
        (Object.entries(MOCK_INVENTORY)
          .map(
            ([k, v]) =>
              `${k} ` +
              (v.byLength
                ? Object.entries(v.byLength)
                    .map(([l, q]) => `${q}×${l}mm`)
                    .join(",")
                : `qty${v.qty ?? 0}`),
          )
          .join(" | ") || "(empty)"),
    );
    if (plan.items.length === 0) lines.push("plan: (no items yet)");
    plan.items.forEach((it, i) =>
      lines.push(
        `item ${it.material_key}${it.unit_length_mm ? ` @${it.unit_length_mm}mm` : ""} qty${it.qty} stock${alloc[i]?.inStock ?? 0} missing${alloc[i]?.missing ?? it.qty}`,
      ),
    );
    for (const [k, s] of Object.entries(plan.stock))
      lines.push(`seller ${k}: ${s.map((x) => `${x.length_mm}mm@${x.price}₪`).join(",")}`);
    for (const [k, p] of Object.entries(plan.platePrice))
      lines.push(`plate price ${k}: ${p.price}₪ ${p.unit}`);
    lines.push(`kerf ${plan.kerf_mm}mm`);
    for (const [k, r] of Object.entries(results))
      lines.push(`optimized ${k}: ${r.result.total_cost}₪ waste${r.result.waste_pct}%`);
    setViewSection("panel", lines.join("\n"));
    return () => setViewSection("panel", null);
  }, [plan, alloc, results]);

  /* ---------- derived costs ---------- */

  const plateCost = (it: PlanItem, missing: number): number | null => {
    const pp = plan.platePrice[it.material_key];
    if (!pp || missing <= 0) return missing <= 0 ? 0 : null;
    const basis = pp.unit === "per_kg" ? plateWeightKg(it) : plateVolumeM3(it);
    return basis == null ? null : missing * basis * pp.price;
  };

  const barMaterials = [
    ...new Set(
      plan.items
        .filter((it, i) => !isPlate(it) && (alloc[i]?.missing ?? 0) > 0)
        .map((it) => it.material_key),
    ),
  ];
  const plateItems = plan.items
    .map((it, i) => ({ it, missing: alloc[i]?.missing ?? 0 }))
    .filter(({ it, missing }) => isPlate(it) && missing > 0);

  const barsCost = Object.values(results).reduce(
    (a, r) => a + r.result.total_cost,
    0,
  );
  const platesCost = plateItems.reduce(
    (a, { it, missing }) => a + (plateCost(it, missing) ?? 0),
    0,
  );
  const totalMissing = alloc.reduce((a, x) => a + x.missing, 0);

  /* ---------- UI ---------- */

  function addFromDraft() {
    const qty = Number(draft.qty);
    const len = draft.length.trim() === "" ? undefined : Number(draft.length);
    const key = draft.material.trim().toUpperCase();
    if (!key || Number.isNaN(qty) || qty <= 0) return;
    if (len == null && !/^PLATE-/i.test(key)) {
      setError("a bar needs a length (or use a PLATE-thk-WxH key)");
      return;
    }
    setError(null);
    addItem(key, qty, len);
    setDraft({ material: "", length: "", qty: "" });
  }

  return (
    <div className="flex h-full min-h-0 flex-col gap-4">
      {/* stage strip — full width, above the columns */}
      <div className="flex shrink-0 items-center justify-between rounded border border-zinc-800 px-4 py-2">
        <div className="text-sm">
          {editing ? (
            <>
              <span className="font-medium">Stage 1 — parts list.</span>{" "}
              <span className="text-zinc-400">
                Build the list in conversation ({plan.items.length} part
                {plan.items.length === 1 ? "" : "s"} so far), then approve.
              </span>
            </>
          ) : (
            <>
              <span className="font-medium">Stage 2 — buy & optimize.</span>{" "}
              <span className="text-zinc-400">
                {totalMissing} piece{totalMissing === 1 ? "" : "s"} missing — price
                and optimize the order.
              </span>
            </>
          )}
        </div>
        {editing ? (
          <div className="flex items-center gap-2">
            {plan.items.length > 0 && (
              <button
                onClick={() => {
                  clearPlan(projectId);
                  setPlan(emptyPlan());
                  setResults({});
                }}
                className="rounded px-2 py-1 text-xs text-zinc-500 hover:bg-zinc-800 hover:text-red-400"
              >
                clear
              </button>
            )}
            <button
              onClick={() => setPlan((p) => ({ ...p, stage: "approved" }))}
              disabled={plan.items.length === 0}
              className="rounded bg-emerald-700 px-3 py-1.5 text-sm font-medium hover:bg-emerald-600 disabled:opacity-40"
            >
              Approve plan →
            </button>
          </div>
        ) : (
          <button
            onClick={() => setPlan((p) => ({ ...p, stage: "draft" }))}
            className="rounded bg-zinc-800 px-3 py-1.5 text-sm hover:bg-zinc-700"
          >
            ← Reopen plan
          </button>
        )}
      </div>

      <div className="flex min-h-0 flex-1 gap-4">
      {/* warehouse, always in sight while planning */}
      <div className="w-56 shrink-0 overflow-y-auto rounded border border-zinc-800 bg-zinc-950 p-3">
        <div className="mb-2 text-sm font-medium">📦 Warehouse</div>
        <ul className="flex flex-col gap-2 text-xs">
          {Object.entries(MOCK_INVENTORY).map(([k, v]) => {
            const inPlan = plan.items.some((it) => it.material_key === k);
            return (
              <li key={k}>
                <div
                  className={`font-medium ${
                    inPlan ? "text-emerald-300" : "text-zinc-200"
                  }`}
                >
                  {k}
                </div>
                <div className="text-zinc-500">
                  {v.byLength
                    ? Object.entries(v.byLength)
                        .map(([l, q]) => `${q}×${l}mm`)
                        .join(", ")
                    : `qty ${v.qty ?? 0}`}
                </div>
              </li>
            );
          })}
          {Object.keys(MOCK_INVENTORY).length === 0 && (
            <li className="text-zinc-500">warehouse is empty</li>
          )}
        </ul>
      </div>

      {/* the plan board — stage 2 only; stage 1 is pure conversation */}
      {!editing && (
      <div className="flex min-w-0 flex-1 flex-col gap-4 overflow-y-auto">
        {error && (
          <div className="rounded border border-red-800 bg-red-950 px-3 py-2 text-sm text-red-300">
            {error}
          </div>
        )}

        {/* parts */}
        <div className="rounded border border-zinc-800 p-4">
          <div className="mb-2 flex items-center justify-between">
            <span className="text-sm font-medium">Parts the build needs</span>
            {editing && (
              <button
                onClick={() => {
                  clearPlan(projectId);
                  setPlan(emptyPlan());
                  setResults({});
                }}
                disabled={plan.items.length === 0}
                className="rounded px-2 py-0.5 text-xs text-zinc-500 hover:bg-zinc-800 hover:text-red-400 disabled:opacity-40"
              >
                clear plan
              </button>
            )}
          </div>
          <table className="w-full text-sm">
            <thead className="text-left text-xs text-zinc-500">
              <tr>
                <th className="px-2 py-1 font-normal">Material</th>
                <th className="px-2 py-1 text-right font-normal">Length mm</th>
                <th className="px-2 py-1 text-right font-normal">Qty</th>
                <th className="px-2 py-1 text-right font-normal">In stock</th>
                <th className="px-2 py-1 text-right font-normal text-emerald-400">
                  Missing
                </th>
                {editing && <th className="px-2 py-1"></th>}
              </tr>
            </thead>
            <tbody>
              {plan.items.map((it, i) => (
                <tr key={it.id} className="border-t border-zinc-800/60">
                  <td className="px-2 py-1 font-medium">{it.material_key}</td>
                  <td className="px-2 py-1 text-right tabular-nums">
                    {it.unit_length_mm ?? "—"}
                  </td>
                  <td className="px-2 py-1 text-right tabular-nums">{it.qty}</td>
                  <td className="px-2 py-1 text-right tabular-nums text-zinc-500">
                    {alloc[i]?.inStock ?? 0}
                  </td>
                  <td
                    className={`px-2 py-1 text-right font-medium tabular-nums ${
                      (alloc[i]?.missing ?? 0) > 0
                        ? "text-emerald-300"
                        : "text-zinc-600"
                    }`}
                  >
                    {alloc[i]?.missing ?? 0}
                  </td>
                  {editing && (
                    <td className="px-2 py-1 text-right">
                      <button
                        onClick={() =>
                          setPlan((p) => ({
                            ...p,
                            items: p.items.filter((x) => x.id !== it.id),
                          }))
                        }
                        className="rounded px-1.5 text-zinc-500 hover:text-red-400"
                      >
                        ✕
                      </button>
                    </td>
                  )}
                </tr>
              ))}
              {plan.items.length === 0 && (
                <tr className="border-t border-zinc-800/60">
                  <td colSpan={6} className="px-2 py-3 text-center text-xs text-zinc-500">
                    No parts yet — tell the assistant what you're building, or add below.
                  </td>
                </tr>
              )}
              {editing && (
                <tr className="border-t border-zinc-800/60">
                  <td className="px-2 py-1.5">
                    <input
                      value={draft.material}
                      onChange={(e) =>
                        setDraft((d) => ({ ...d, material: e.target.value }))
                      }
                      placeholder="e.g. L50X50X5"
                      title="Profile key (L50X50X5) or plate key (PLATE-10-200X300)"
                      className={`${inputCls} w-44`}
                    />
                  </td>
                  <td className="px-2 py-1.5 text-right">
                    <input
                      value={draft.length}
                      onChange={(e) =>
                        setDraft((d) => ({ ...d, length: e.target.value }))
                      }
                      placeholder="bars only"
                      className={`${inputCls} w-24 text-right`}
                    />
                  </td>
                  <td className="px-2 py-1.5 text-right">
                    <input
                      value={draft.qty}
                      onChange={(e) => setDraft((d) => ({ ...d, qty: e.target.value }))}
                      placeholder="qty"
                      className={`${inputCls} w-16 text-right`}
                    />
                  </td>
                  <td colSpan={3} className="px-2 py-1.5">
                    <button
                      onClick={addFromDraft}
                      className="rounded bg-zinc-800 px-2.5 py-1 text-xs hover:bg-zinc-700"
                    >
                      + add part
                    </button>
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        {/* stage 2: buy & optimize */}
        {!editing && (
          <>
            {barMaterials.map((key) => {
              const lines = plan.stock[key] ?? [];
              return (
                <div key={key} className="rounded border border-zinc-800 p-4">
                  <div className="mb-2 flex items-baseline justify-between">
                    <span className="font-medium">{key} — buy</span>
                    <span className="text-xs text-zinc-500">
                      missing pieces to cut from new bars
                    </span>
                  </div>
                  <div className="mb-1 text-xs text-zinc-400">
                    Seller's stock lengths &amp; prices
                  </div>
                  {(lines.length ? lines : [{ length_mm: 0, price: 0 }]).map(
                    (s, i) => (
                      <div key={i} className="mb-1 flex items-center gap-2">
                        <input
                          value={s.length_mm || ""}
                          onChange={(e) => {
                            const next = [
                              ...(lines.length ? lines : [{ length_mm: 0, price: 0 }]),
                            ];
                            next[i] = {
                              ...next[i],
                              length_mm: Number(e.target.value) || 0,
                            };
                            setStockCatalog(key, next);
                          }}
                          placeholder="length mm"
                          className={`${inputCls} w-28`}
                        />
                        <span className="text-xs text-zinc-500">mm @</span>
                        <input
                          value={s.price || ""}
                          onChange={(e) => {
                            const next = [
                              ...(lines.length ? lines : [{ length_mm: 0, price: 0 }]),
                            ];
                            next[i] = { ...next[i], price: Number(e.target.value) || 0 };
                            setStockCatalog(key, next);
                          }}
                          placeholder="price"
                          className={`${inputCls} w-24`}
                        />
                        <span className="text-xs text-zinc-500">₪ / bar</span>
                        {lines.length > 1 && (
                          <button
                            onClick={() =>
                              setStockCatalog(key, lines.filter((_, j) => j !== i))
                            }
                            className="rounded px-1.5 text-zinc-500 hover:text-red-400"
                          >
                            ✕
                          </button>
                        )}
                      </div>
                    ),
                  )}
                  <button
                    onClick={() =>
                      setStockCatalog(key, [...lines, { length_mm: 0, price: 0 }])
                    }
                    className="mt-1 rounded bg-zinc-800 px-2 py-1 text-xs hover:bg-zinc-700"
                  >
                    + stock length
                  </button>
                  {results[key] && <OrderResult shown={results[key]} />}
                </div>
              );
            })}

            {plateItems.length > 0 && (
              <div className="rounded border border-zinc-800 p-4">
                <div className="mb-2 font-medium">Plates — buy</div>
                <table className="w-full text-sm">
                  <thead className="text-left text-xs text-zinc-500">
                    <tr>
                      <th className="px-2 py-1 font-normal">Plate</th>
                      <th className="px-2 py-1 text-right font-normal">Missing</th>
                      <th className="px-2 py-1 font-normal">Price</th>
                      <th className="px-2 py-1 font-normal">Unit</th>
                      <th className="px-2 py-1 text-right font-normal">Cost ₪</th>
                    </tr>
                  </thead>
                  <tbody>
                    {plateItems.map(({ it, missing }) => {
                      const pp = plan.platePrice[it.material_key];
                      const cost = plateCost(it, missing);
                      return (
                        <tr key={it.id} className="border-t border-zinc-800/60">
                          <td className="px-2 py-1.5 font-medium">
                            {it.material_key}
                          </td>
                          <td className="px-2 py-1.5 text-right tabular-nums">
                            {missing}
                          </td>
                          <td className="px-2 py-1.5">
                            <input
                              value={pp?.price ?? ""}
                              onChange={(e) =>
                                setPlatePrice(
                                  it.material_key,
                                  Number(e.target.value) || 0,
                                  pp?.unit ?? "per_kg",
                                )
                              }
                              placeholder="price"
                              className={`${inputCls} w-24`}
                            />
                          </td>
                          <td className="px-2 py-1.5">
                            <select
                              value={pp?.unit ?? "per_kg"}
                              onChange={(e) =>
                                setPlatePrice(
                                  it.material_key,
                                  pp?.price ?? 0,
                                  e.target.value as PlateUnit,
                                )
                              }
                              className="rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-sm"
                            >
                              <option value="per_kg">₪ / kg</option>
                              <option value="per_m3">₪ / m³</option>
                            </select>
                          </td>
                          <td className="px-2 py-1.5 text-right tabular-nums">
                            {cost != null ? cost.toFixed(0) : "need dims"}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}

            <div className="flex items-center gap-4 rounded border border-zinc-800 p-4">
              <label className="flex items-center gap-2 text-xs text-zinc-400">
                Kerf mm
                <input
                  value={plan.kerf_mm}
                  onChange={(e) =>
                    setPlan((p) => ({ ...p, kerf_mm: Number(e.target.value) || 0 }))
                  }
                  className={`${inputCls} w-16`}
                />
              </label>
              <button
                onClick={() =>
                  void optimize().catch((e) => setError((e as Error).message))
                }
                disabled={busy || barMaterials.length === 0}
                className="rounded bg-emerald-700 px-4 py-1.5 text-sm font-medium hover:bg-emerald-600 disabled:opacity-40"
              >
                {busy ? "Optimizing…" : "Optimize buy"}
              </button>
              <div className="ml-auto text-sm text-zinc-400">
                bars {barsCost.toFixed(0)} ₪ · plates {platesCost.toFixed(0)} ₪ ·{" "}
                <span className="text-lg font-medium text-emerald-300">
                  {(barsCost + platesCost).toFixed(0)} ₪
                </span>
              </div>
            </div>
          </>
        )}
      </div>

      )}

      {/* planning conversation — on the right, like the dock everywhere else;
          in stage 1 it takes all the room the board isn't using */}
      <div
        className={`flex min-w-0 flex-col rounded border border-zinc-800 bg-zinc-950 ${
          editing ? "flex-1" : "w-96 shrink-0"
        }`}
      >
        <ChatPanel
          scope="project"
          scopeId={projectId}
          hint="Plan a build together — it knows the warehouse"
          screenContext={readViewContext}
          onAction={onAction}
          toolsBlock={PLAN_TOOLS}
        />
      </div>
      </div>
    </div>
  );
}
