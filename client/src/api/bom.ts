// BOM rows (shape, size, quantity, cut length) are built server-side — see
// server/app/bom/. Only the operator's local view preferences live here.

const LS_HIDDEN_PREFIX = "bom_hidden_";
const LS_SUMMARY_KEY = "summary_included_docs";

/** BOM rows the operator has hidden from this document's summary. */
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

/** Documents included in the aggregated summary. null = never chosen; include all. */
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
