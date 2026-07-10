import type { CutoutOut } from "./types";
import { PT_TO_MM } from "./wkt";

export const SHAPE_LABEL: Record<string, string> = {
  hole: "Circle",
  slot: "Slot",
  notch: "Notch",
  freeform: "Irregular",
};

export interface BomGroup {
  key: string;
  shape: string;
  dims: string;
  active: CutoutOut[];
  rejected: CutoutOut[];
}

const round = (v: number, step: number) => Math.round(v / step) * step;

export function dimsSignature(c: CutoutOut): string {
  if (c.measured_dims_json) {
    try {
      const d = JSON.parse(c.measured_dims_json) as Record<string, number>;
      if (d.diameter_mm != null) return `Ø ${round(d.diameter_mm, 0.5).toFixed(1)} mm`;
      if (d.length_mm != null && d.width_mm != null)
        return `${round(d.length_mm, 0.5).toFixed(1)}×${round(d.width_mm, 0.5).toFixed(1)} mm`;
    } catch {
      /* fall through to bbox */
    }
  }
  const [x0, y0, x1, y1] = c.bbox;
  const w = round((x1 - x0) * PT_TO_MM, 1);
  const h = round((y1 - y0) * PT_TO_MM, 1);
  return `~${Math.max(w, h)}×${Math.min(w, h)} mm`;
}

export function buildGroups(cutouts: CutoutOut[]): BomGroup[] {
  const groups = new Map<string, BomGroup>();
  for (const c of cutouts) {
    const dims = c.kind === "freeform" ? "Custom poly-path" : dimsSignature(c);
    const key = `${c.kind}|${dims}`;
    let g = groups.get(key);
    if (!g) {
      g = { key, shape: SHAPE_LABEL[c.kind] ?? c.kind, dims, active: [], rejected: [] };
      groups.set(key, g);
    }
    (c.status === "rejected" ? g.rejected : g.active).push(c);
  }
  return [...groups.values()].sort((a, b) => b.active.length - a.active.length);
}

// --- localStorage helpers ---

const LS_HIDDEN_PREFIX = "bom_hidden_";
const LS_SUMMARY_KEY = "summary_included_docs";

export function loadHiddenKeys(docId: number): Set<string> {
  try {
    const v = localStorage.getItem(LS_HIDDEN_PREFIX + docId);
    if (v) return new Set(JSON.parse(v) as string[]);
  } catch {
    /* ignore */
  }
  return new Set();
}

export function saveHiddenKeys(docId: number, keys: Set<string>): void {
  try {
    localStorage.setItem(LS_HIDDEN_PREFIX + docId, JSON.stringify([...keys]));
  } catch {
    /* ignore */
  }
}

export function getSummaryIncludes(): Set<number> | null {
  try {
    const v = localStorage.getItem(LS_SUMMARY_KEY);
    if (v === null) return null;
    return new Set(JSON.parse(v) as number[]);
  } catch {
    return null;
  }
}

export function setSummaryIncludes(ids: Set<number>): void {
  try {
    localStorage.setItem(LS_SUMMARY_KEY, JSON.stringify([...ids]));
  } catch {
    /* ignore */
  }
}

export function addToSummaryIncludes(docId: number): void {
  const current = getSummaryIncludes() ?? new Set<number>();
  current.add(docId);
  setSummaryIncludes(current);
}
