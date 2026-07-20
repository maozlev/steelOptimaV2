// Display-only classification of a canonical material_key into a coarse category.
//
// The only reliable signal is the key prefix the server's canonical_material_key
// emits: plates come out as "PLATE-<thk>-<w>X<h>", everything else is a rolled
// profile ("L60X60X6", "HEA200", "D60-L18"). This is a UI grouping aid ONLY — if
// a category ever needs to drive logic (pricing, export, which rows an optimizer
// eats) it must move to server-side normalize.py so there is one source of truth.

export type MaterialCategory = "bar" | "plate" | "other";

export function materialCategory(key: string): MaterialCategory {
  if (/^PLATE\b|^PLATE-/i.test(key)) return "plate";
  if (/^[A-Za-z]+\d/.test(key)) return "bar"; // profile designator + dims
  return "other"; // "(unidentified)" or a raw description that never parsed
}

export const CATEGORY_LABEL: Record<MaterialCategory, string> = {
  bar: "Bars",
  plate: "Plates",
  other: "Other",
};

// stable render order; skip empties at the call site
export const CATEGORY_ORDER: MaterialCategory[] = ["bar", "plate", "other"];
