import { Fragment, useEffect, useMemo, useState } from "react";
import { loadHiddenKeys, saveHiddenKeys } from "../api/bom";
import type { BomRow, BomTotals, CutoutOut } from "../api/types";

/** Cut length reads in mm up close and in metres once it stops being a hole. */
export function formatLength(mm: number): string {
  return mm >= 1000 ? `${(mm / 1000).toFixed(2)} m` : `${mm.toFixed(1)} mm`;
}

function EyeIcon({ open }: { open: boolean }) {
  return open ? (
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" className="h-3.5 w-3.5">
      <path d="M1 8C2.5 4.5 5 3 8 3s5.5 1.5 7 5c-1.5 3.5-4 5-7 5S2.5 11.5 1 8Z" />
      <circle cx="8" cy="8" r="1.8" />
    </svg>
  ) : (
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" className="h-3.5 w-3.5">
      <path d="M1 8C2.5 4.5 5 3 8 3s5.5 1.5 7 5c-1.5 3.5-4 5-7 5S2.5 11.5 1 8Z" strokeOpacity="0.4" />
      <circle cx="8" cy="8" r="1.8" strokeOpacity="0.4" />
      <line x1="3" y1="13" x2="13" y2="3" strokeOpacity="0.8" />
    </svg>
  );
}

export default function BomPanel({
  docId,
  rows,
  cutouts,
  finalizeThreshold,
  locked,
  busy,
  onHighlight,
  onReject,
  onRestore,
  onRejectGroup,
  onFinalize,
}: {
  docId: number;
  rows: BomRow[];
  /** Every cutout in the document, across all pages — drives the finalize preview. */
  cutouts: CutoutOut[];
  finalizeThreshold: number;
  locked: boolean;
  busy: boolean;
  onHighlight: (ids: number[] | null) => void;
  onReject: (id: number) => void;
  onRestore: (id: number) => void;
  onRejectGroup: (ids: number[]) => void;
  onFinalize: () => void;
}) {
  const [expandedKey, setExpandedKey] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [hiddenKeys, setHiddenKeys] = useState<Set<string>>(() => loadHiddenKeys(docId));
  const [confirmDeleteKey, setConfirmDeleteKey] = useState<string | null>(null);

  useEffect(() => {
    saveHiddenKeys(docId, hiddenKeys);
  }, [docId, hiddenKeys]);

  const byId = useMemo(() => new Map(cutouts.map((c) => [c.id, c])), [cutouts]);
  const visibleRows = rows.filter((r) => !hiddenKeys.has(r.key));
  const hiddenCount = rows.filter((r) => hiddenKeys.has(r.key)).length;

  // Totals reflect what is actually shown, so hiding a row visibly changes them.
  const shownTotals: BomTotals = useMemo(
    () => ({
      qty: visibleRows.reduce((s, r) => s + r.qty, 0),
      cut_length_mm: visibleRows.reduce((s, r) => s + r.cut_length_total_mm, 0),
      pending_qty: visibleRows.reduce((s, r) => s + r.pending_qty, 0),
    }),
    [visibleRows],
  );

  const willApprove = cutouts.filter(
    (c) => c.status === "pending" && c.confidence >= finalizeThreshold,
  ).length;
  const willReject = cutouts.filter(
    (c) => c.status === "pending" && c.confidence < finalizeThreshold,
  ).length;

  function toggleVisibility(row: BomRow, e: React.MouseEvent) {
    e.stopPropagation();
    setHiddenKeys((prev) => {
      const next = new Set(prev);
      if (next.has(row.key)) {
        next.delete(row.key);
      } else {
        next.add(row.key);
        if (expandedKey === row.key) {
          setExpandedKey(null);
          onHighlight(null);
        }
      }
      return next;
    });
  }

  function rowStatus(row: BomRow) {
    if (row.pending_qty === 0) {
      const members = row.cutout_ids.map((id) => byId.get(id)).filter(Boolean);
      if (members.length && members.every((c) => c!.source === "manual"))
        return <span className="text-violet-300">✓ Manually added</span>;
      return <span className="text-emerald-300">✓ Verified</span>;
    }
    const lowConf = row.cutout_ids.some((id) => {
      const c = byId.get(id);
      return c?.status === "pending" && c.confidence < finalizeThreshold;
    });
    return (
      <span className={lowConf ? "text-red-400" : "text-amber-300"}>
        ▲ {row.pending_qty} under review
      </span>
    );
  }

  function toggleRow(row: BomRow) {
    const next = expandedKey === row.key ? null : row.key;
    setExpandedKey(next);
    onHighlight(next ? row.cutout_ids : null);
  }

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-zinc-800 px-3 py-2 text-xs font-medium text-zinc-300">
        Bill of Materials — whole document
      </div>
      <div className="flex-1 overflow-auto">
        {rows.length === 0 ? (
          <p className="p-4 text-center text-xs text-zinc-600">
            No cutouts yet — run extraction first.
          </p>
        ) : (
          <>
            <table className="w-full text-xs">
              <thead className="sticky top-0 bg-zinc-950 text-left text-zinc-500">
                <tr>
                  <th className="w-14 px-2 py-1.5 font-normal" />
                  <th className="px-1 py-1.5 font-normal">Shape</th>
                  <th className="px-1 py-1.5 font-normal">Dimensions</th>
                  <th className="px-1 py-1.5 text-right font-normal">Qty</th>
                  <th className="px-1 py-1.5 text-right font-normal">Cut ea.</th>
                  <th className="px-1 py-1.5 text-right font-normal">Cut total</th>
                  <th className="px-3 py-1.5 text-right font-normal">Status</th>
                </tr>
              </thead>
              <tbody>
                {visibleRows.map((row) => (
                  <Fragment key={row.key}>
                    {confirmDeleteKey === row.key ? (
                      <tr className="border-t border-zinc-800/60 bg-red-950/30">
                        <td colSpan={4} className="px-3 py-2 text-xs text-red-300">
                          Reject all {row.qty} cutouts in this group?
                        </td>
                        <td colSpan={3} className="px-3 py-2 text-right">
                          <div className="flex justify-end gap-1">
                            <button
                              onClick={() => {
                                setConfirmDeleteKey(null);
                                onRejectGroup(row.cutout_ids);
                              }}
                              className="rounded bg-red-700 px-2 py-0.5 text-xs font-medium hover:bg-red-600"
                            >
                              Reject all
                            </button>
                            <button
                              onClick={() => setConfirmDeleteKey(null)}
                              className="rounded bg-zinc-800 px-2 py-0.5 text-xs hover:bg-zinc-700"
                            >
                              Cancel
                            </button>
                          </div>
                        </td>
                      </tr>
                    ) : (
                      <tr
                        onClick={() => toggleRow(row)}
                        className={`cursor-pointer border-t border-zinc-800/60 hover:bg-zinc-900 ${
                          expandedKey === row.key ? "bg-zinc-800/60" : ""
                        }`}
                      >
                        <td className="px-2 py-2">
                          <div className="flex gap-1.5">
                            <button
                              onClick={(e) => toggleVisibility(row, e)}
                              title="Hide from summary"
                              className="text-zinc-500 hover:text-zinc-200"
                            >
                              <EyeIcon open={true} />
                            </button>
                            {!locked && (
                              <button
                                onClick={(e) => {
                                  e.stopPropagation();
                                  setConfirmDeleteKey(row.key);
                                }}
                                title="Reject entire group"
                                className="text-zinc-600 hover:text-red-400"
                              >
                                <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" className="h-3.5 w-3.5">
                                  <polyline points="2,4 14,4" />
                                  <path d="M5 4V3a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v1" />
                                  <rect x="3" y="4" width="10" height="9" rx="1" />
                                  <line x1="6" y1="7" x2="6" y2="11" />
                                  <line x1="10" y1="7" x2="10" y2="11" />
                                </svg>
                              </button>
                            )}
                          </div>
                        </td>
                        <td className="px-1 py-2 font-medium text-zinc-200">{row.shape_label}</td>
                        <td className="px-1 py-2 text-zinc-400">{row.dims}</td>
                        <td className="px-1 py-2 text-right tabular-nums text-zinc-200">
                          {row.qty}x
                        </td>
                        <td className="px-1 py-2 text-right tabular-nums text-zinc-500">
                          {formatLength(row.cut_length_each_mm)}
                        </td>
                        <td className="px-1 py-2 text-right tabular-nums text-zinc-300">
                          {formatLength(row.cut_length_total_mm)}
                        </td>
                        <td className="px-3 py-2 text-right">{rowStatus(row)}</td>
                      </tr>
                    )}
                    {expandedKey === row.key && confirmDeleteKey !== row.key &&
                      [...row.cutout_ids, ...row.rejected_ids]
                        .map((id) => byId.get(id))
                        .filter((c): c is CutoutOut => !!c)
                        .map((c) => (
                          <tr key={`m-${c.id}`} className="bg-zinc-900/50 text-zinc-400">
                            <td className="py-1 pl-8 pr-1" colSpan={5}>
                              <span className={c.status === "rejected" ? "line-through" : ""}>
                                #{c.id} · {c.status} · {c.source}
                              </span>
                            </td>
                            <td className="px-1 py-1 text-right tabular-nums">
                              {c.confidence.toFixed(2)}
                            </td>
                            <td className="px-3 py-1 text-right">
                              {!locked &&
                                (c.status === "rejected" ? (
                                  <button
                                    disabled={busy}
                                    onClick={() => onRestore(c.id)}
                                    className="rounded bg-zinc-800 px-1.5 py-0.5 hover:bg-zinc-700 disabled:opacity-50"
                                  >
                                    restore
                                  </button>
                                ) : (
                                  <button
                                    disabled={busy}
                                    onClick={() => onReject(c.id)}
                                    className="rounded bg-zinc-800 px-1.5 py-0.5 text-red-300 hover:bg-zinc-700 disabled:opacity-50"
                                  >
                                    remove
                                  </button>
                                ))}
                            </td>
                          </tr>
                        ))}
                  </Fragment>
                ))}
              </tbody>
              <tfoot className="sticky bottom-0 bg-zinc-950">
                <tr className="border-t-2 border-zinc-700 font-semibold text-zinc-100">
                  <td />
                  <td className="px-1 py-2" colSpan={2}>
                    Total
                  </td>
                  <td className="px-1 py-2 text-right tabular-nums">{shownTotals.qty}x</td>
                  <td />
                  <td className="px-1 py-2 text-right tabular-nums text-emerald-300">
                    {formatLength(shownTotals.cut_length_mm)}
                  </td>
                  <td className="px-3 py-2 text-right text-xs font-normal text-zinc-500">
                    cut length
                  </td>
                </tr>
              </tfoot>
            </table>
            {hiddenCount > 0 && (
              <div className="border-t border-zinc-800/60 px-3 py-2 text-xs text-zinc-500">
                <span>{hiddenCount} row{hiddenCount > 1 ? "s" : ""} hidden · </span>
                <button
                  onClick={() => setHiddenKeys(new Set())}
                  className="text-zinc-400 underline hover:text-zinc-200"
                >
                  show all
                </button>
                <span className="ml-2 text-zinc-700">
                  ({rows
                    .filter((r) => hiddenKeys.has(r.key))
                    .map((r) => `${r.shape_label} ${r.dims}`)
                    .join(", ")})
                </span>
              </div>
            )}
          </>
        )}
      </div>
      <div className="border-t border-zinc-800 p-3">
        {locked ? (
          <div className="rounded bg-emerald-950/50 px-3 py-2 text-center text-xs text-emerald-300">
            ✓ Work order approved &amp; locked
          </div>
        ) : confirming ? (
          <div className="flex flex-col gap-2 text-xs">
            <p className="text-zinc-400">
              Finalizing will approve{" "}
              <span className="text-emerald-300">{willApprove}</span> high-confidence and
              reject <span className="text-red-300">{willReject}</span> unreviewed
              low-confidence cutouts, then lock the document.
            </p>
            <div className="flex gap-2">
              <button
                disabled={busy}
                onClick={() => {
                  setConfirming(false);
                  onFinalize();
                }}
                className="flex-1 rounded bg-emerald-700 px-3 py-1.5 font-medium hover:bg-emerald-600 disabled:opacity-50"
              >
                Confirm
              </button>
              <button
                onClick={() => setConfirming(false)}
                className="rounded bg-zinc-800 px-3 py-1.5 hover:bg-zinc-700"
              >
                Back
              </button>
            </div>
          </div>
        ) : (
          <button
            disabled={busy || cutouts.length === 0}
            onClick={() => setConfirming(true)}
            className="w-full rounded bg-emerald-700 px-3 py-2 text-sm font-semibold hover:bg-emerald-600 disabled:opacity-50"
          >
            APPROVE &amp; FINALIZE WORK ORDER
          </button>
        )}
      </div>
    </div>
  );
}
