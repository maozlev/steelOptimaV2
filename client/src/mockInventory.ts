// MOCK inventory — a client-side stand-in for a real, server-side stock table.
//
// Keyed by canonical material_key. Bars carry per-length stock ({unit_length_mm:
// qty}); plates carry a flat qty (they are not cut to length). Numbers below are
// seeded from project 11's approved summary with DELIBERATE GAPS — some lengths
// fully covered, some partial, some absent — so the "need to order = required −
// in stock" flow has something to chew on.
//
// When inventory goes real this moves to the server (aggregate.py) so Summary,
// Bid and Orders read one net-demand number instead of each recomputing it.

import type { SummaryRow } from "./api/types";

interface StockEntry {
  byLength?: Record<number, number>; // bars: unit_length_mm -> qty in stock
  qty?: number; // plates: flat qty in stock
}

export const MOCK_INVENTORY: Record<string, StockEntry> = {
  // bars — some lengths stocked, some not
  L160X160X15: { byLength: { 9000: 2 } }, // need 4×9000, have 2 → order 2
  L60X60X6: { byLength: { 743: 8, 953: 8, 2396: 4 } }, // two lengths full, 2396 partial, rest 0
  L70X70X7: { byLength: { 2864: 8, 4370: 4 } }, // 2864 full, 4370 partial, rest 0
  L80X80X8: { byLength: { 3907: 4 } }, // fully covered → nothing to order
  // L90X90X9, L120X120X11: not stocked at all → order everything

  // plates — flat qty
  "PLATE-16-890X185": { qty: 4 }, // need 8, have 4
  "PLATE-12-345X310": { qty: 4 }, // fully covered
  // PLATE-14-450X174, PLATE-6-80X40: not stocked
};

export interface NetDemand {
  grossQty: number;
  inStockQty: number;
  netQty: number;
  // per-length net pieces for the cutting optimizer (bars only; empty for plates)
  netLengths: { unit_length_mm: number; qty: number }[];
  // netQty / grossQty — scales length/weight/price, which are all linear in qty
  factor: number;
  known: boolean; // is this material in the inventory at all?
}

export function netDemand(row: SummaryRow): NetDemand {
  const inv = MOCK_INVENTORY[row.material_key];
  const gross = row.qty;

  if (!inv) {
    return {
      grossQty: gross,
      inStockQty: 0,
      netQty: gross,
      netLengths: row.lengths.map((l) => ({ ...l })),
      factor: 1,
      known: false,
    };
  }

  if (row.lengths.length > 0) {
    // bar: subtract stock length-by-length
    const byLength = inv.byLength ?? {};
    const netLengths = row.lengths
      .map((l) => ({
        unit_length_mm: l.unit_length_mm,
        qty: Math.max(0, l.qty - (byLength[l.unit_length_mm] ?? 0)),
      }))
      .filter((l) => l.qty > 0);
    const netQty = netLengths.reduce((a, l) => a + l.qty, 0);
    return {
      grossQty: gross,
      inStockQty: gross - netQty,
      netQty,
      netLengths,
      factor: gross > 0 ? netQty / gross : 0,
      known: true,
    };
  }

  // plate: flat qty
  const stock = inv.qty ?? 0;
  const netQty = Math.max(0, gross - stock);
  return {
    grossQty: gross,
    inStockQty: gross - netQty,
    netQty,
    netLengths: [],
    factor: gross > 0 ? netQty / gross : 0,
    known: true,
  };
}
