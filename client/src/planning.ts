// Free-planning model (client-side prototype): one live plan per project,
// persisted to localStorage, computed against the mock inventory. Planning only
// COMPUTES against stock — it never mutates inventory (Maoz's call: reserving/
// consuming stock is a later, explicit step).

import { materialCategory } from "./materials";
import { MOCK_INVENTORY } from "./mockInventory";

export type PlateUnit = "per_kg" | "per_m3";

export interface PlanItem {
  id: number;
  material_key: string; // free text, canonicalized to upper-case
  qty: number;
  unit_length_mm?: number; // bars: the cut length this item needs
  thk_mm?: number; // plates: explicit dims (else parsed from a PLATE-… key)
  w_mm?: number;
  h_mm?: number;
}

export interface StockLine {
  length_mm: number;
  price: number;
}

// Two-stage flow: build the parts list first ("draft"), approve it, and only
// then move to the buy/optimization stage ("approved").
export type PlanStage = "draft" | "approved";

export interface PlanState {
  stage: PlanStage;
  items: PlanItem[];
  stock: Record<string, StockLine[]>; // seller bar catalog per material
  platePrice: Record<string, { price: number; unit: PlateUnit }>;
  kerf_mm: number;
  nextId: number;
  // The drawing question: use warehouse pieces where they fit, or treat the
  // drawing's dimensions as authoritative and order everything new?
  // null = not answered yet — the UI asks once items exist.
  useInventory: boolean | null;
}

export const emptyPlan = (): PlanState => ({
  stage: "draft",
  items: [],
  stock: {},
  platePrice: {},
  kerf_mm: 3,
  nextId: 1,
  useInventory: null,
});

const storageKey = (projectId: number) => `steeloptima.plan.${projectId}`;

export function loadPlan(projectId: number): PlanState {
  try {
    const raw = localStorage.getItem(storageKey(projectId));
    if (raw) return { ...emptyPlan(), ...(JSON.parse(raw) as PlanState) };
  } catch {
    /* corrupted state → start clean */
  }
  return emptyPlan();
}

export function savePlan(projectId: number, plan: PlanState): void {
  localStorage.setItem(storageKey(projectId), JSON.stringify(plan));
}

export function clearPlan(projectId: number): void {
  localStorage.removeItem(storageKey(projectId));
}

export function isPlate(item: PlanItem): boolean {
  return item.thk_mm != null || materialCategory(item.material_key) === "plate";
}

// "PLATE-14-450X174" → {thk:14, w:450, h:174}
const PLATE_KEY_RE = /^PLATE-(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)X(\d+(?:\.\d+)?)$/i;

export function plateDims(
  item: PlanItem,
): { thk: number; w: number; h: number } | null {
  if (item.thk_mm && item.w_mm && item.h_mm)
    return { thk: item.thk_mm, w: item.w_mm, h: item.h_mm };
  const m = PLATE_KEY_RE.exec(item.material_key);
  return m ? { thk: +m[1], w: +m[2], h: +m[3] } : null;
}

const STEEL_KG_PER_M3 = 7850;

export function plateVolumeM3(item: PlanItem): number | null {
  const d = plateDims(item);
  return d ? (d.thk / 1000) * (d.w / 1000) * (d.h / 1000) : null;
}

export function plateWeightKg(item: PlanItem): number | null {
  const v = plateVolumeM3(item);
  return v == null ? null : v * STEEL_KG_PER_M3;
}

export interface Allocation {
  inStock: number;
  missing: number;
}

// Greedy allocation in item order: two lines wanting the same stock pool split
// it first-come-first-served. Bars draw from byLength[len]; plates (and bars
// with no length) draw from the flat qty pool. Unknown materials → all missing.
// useInventory false = order everything new (the drawing's dims are final and
// warehouse offcuts must not be substituted); null behaves like true so the
// numbers are visible while the question is still open.
export function allocate(items: PlanItem[], useInventory: boolean | null = true): Allocation[] {
  if (useInventory === false) {
    return items.map((i) => ({ inStock: 0, missing: i.qty }));
  }
  const pool = new Map<string, number>();
  const poolKey = (i: PlanItem) =>
    isPlate(i) || i.unit_length_mm == null
      ? `${i.material_key}|flat`
      : `${i.material_key}|${i.unit_length_mm}`;
  const initial = (i: PlanItem): number => {
    const inv = MOCK_INVENTORY[i.material_key];
    if (!inv) return 0;
    if (isPlate(i) || i.unit_length_mm == null) return inv.qty ?? 0;
    return inv.byLength?.[i.unit_length_mm] ?? 0;
  };
  return items.map((i) => {
    const k = poolKey(i);
    if (!pool.has(k)) pool.set(k, initial(i));
    const avail = pool.get(k)!;
    const take = Math.min(i.qty, Math.max(avail, 0));
    pool.set(k, avail - take);
    return { inStock: take, missing: i.qty - take };
  });
}
