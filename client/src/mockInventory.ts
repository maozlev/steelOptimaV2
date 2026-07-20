// MOCK inventory — a client-side stand-in for a real, server-side stock table.
//
// Keyed by canonical material_key. Bars carry per-length stock ({unit_length_mm:
// qty}); plates carry a flat qty (they are not cut to length). Numbers below are
// seeded from the project's real profiles/lengths with hundreds of units per
// length (thousands per material), and DELIBERATE GAPS so the "need to order =
// required − in stock" flow still has something to chew on: the long, expensive
// lengths are mostly left short/absent, and L90X90X9 is not stocked at all, so
// Bid & Orders stay non-empty even though most demand is covered.
//
// When inventory goes real this moves to the server (aggregate.py) so Summary,
// Bid and Orders read one net-demand number instead of each recomputing it.

import type { SummaryRow } from "./api/types";

interface StockEntry {
  byLength?: Record<number, number>; // bars: unit_length_mm -> qty in stock
  qty?: number; // plates: flat qty in stock
}

// Mutable at runtime (prototype only): the agent and a future edit UI change
// these numbers in place, and callers re-read after a remount. When inventory
// goes real this is a server table, not a module global.
export const MOCK_INVENTORY: Record<string, StockEntry> = {
  // bars — most lengths deeply stocked; a few long lengths left short/absent
  L160X160X15: {
    byLength: { 503: 320, 521: 280, 626: 450, 751: 300, 752: 200, 778: 520, 9000: 2 },
  }, // 3132 absent, 9000 short (need 4, have 2) → orders
  L100X100X10: {
    byLength: { 535: 400, 639: 260, 777: 300, 2096: 500, 2865: 380, 2892: 240 },
  }, // 5854, 5878 (the long ones) absent → orders
  L60X60X6: {
    byLength: {
      635: 300, 653: 520, 743: 640, 953: 600, 1052: 280, 1087: 260, 1292: 240,
      1357: 560, 1394: 320, 1395: 480, 1466: 600, 1552: 560, 1805: 520,
      1896: 540, 2077: 220, 2188: 600, 2380: 560, 2396: 600,
    },
  }, // 3102, 3112 absent → orders
  L70X70X7: {
    byLength: { 925: 340, 1079: 160, 2619: 280, 2864: 520, 2881: 320, 3099: 260, 5848: 80, 5855: 120 },
  }, // 4370, 5861 absent → orders
  L80X80X8: {
    byLength: { 523: 120, 760: 220, 2865: 400, 3123: 180, 3907: 300 },
  }, // 5882 absent → orders
  // L90X90X9: not stocked at all → order everything
  L120X120X11: {
    byLength: { 903: 180, 1068: 420, 1074: 160, 1077: 90 },
  }, // 2898, 5842 absent → orders
  L50X50X5: {
    byLength: { 536: 400, 647: 120, 650: 340, 745: 420, 1389: 140, 2098: 200, 2884: 260, 3133: 280 },
  }, // 5854 absent → orders

  // plates — flat qty, deeply stocked
  "PLATE-16-890X185": { qty: 200 },
  "PLATE-14-450X174": { qty: 350 },
  "PLATE-12-345X310": { qty: 180 },
  // PLATE-6-80X40: not stocked → order everything
};

// Set stock for one material. A bar length is keyed by unit_length_mm; a plate
// (or a bar with no length given) uses the flat qty. qty 0 zeroes that entry.
export function setInventoryStock(
  materialKey: string,
  opts: { unit_length_mm?: number; qty: number },
): void {
  const cur: StockEntry = MOCK_INVENTORY[materialKey] ?? {};
  if (opts.unit_length_mm != null) {
    cur.byLength = { ...(cur.byLength ?? {}), [opts.unit_length_mm]: opts.qty };
  } else {
    cur.qty = opts.qty;
  }
  MOCK_INVENTORY[materialKey] = cur;
}

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
