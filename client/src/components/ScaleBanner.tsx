import { useEffect, useState } from "react";
import type { ScaleStatus } from "../api/types";

/** "1:5" for a reduction, "2:1" for a magnified sheet. */
export function formatScale(scale: number): string {
  return scale >= 1 ? `1:${+scale.toFixed(3)}` : `${+(1 / scale).toFixed(3)}:1`;
}

/**
 * The sheet scale is the OPERATOR'S call. Every dimension in the BOM is a paper
 * measurement multiplied by it, so nothing is cut from a number nobody signed off on —
 * finalize is blocked until each page is confirmed.
 *
 * The detector no longer decides; it checks. It proposes a value (one click to accept)
 * and, if what the operator sets disagrees with the drawing's own dimensions, it says so
 * loudly. That check is the only thing standing between a mistyped "1:50" on a 1:5 sheet
 * and every part being cut ten times too big — moving the decision to a human removes the
 * machine's mistakes, not the human's.
 */
export default function ScaleBanner({
  scale,
  locked,
  busy,
  extracting,
  onSetScale,
}: {
  scale: ScaleStatus;
  locked: boolean;
  busy: boolean;
  /** the scale is read DURING extraction — until that finishes there is nothing to say */
  extracting: boolean;
  onSetScale: (pageId: number, scale: number) => void;
}) {
  const [entry, setEntry] = useState<Record<number, string>>({});
  const [overriding, setOverriding] = useState(false);

  // pre-fill each page with what the drawing appears to say
  useEffect(() => {
    setEntry((prev) => {
      const next = { ...prev };
      for (const p of scale.pages) {
        if (next[p.page_id] === undefined && p.detected)
          next[p.page_id] = String(+p.detected.toFixed(3));
      }
      return next;
    });
  }, [scale.pages]);

  const unconfirmed = scale.pages.filter((p) => !p.confirmed);
  const disagreeing = scale.pages.filter((p) => p.confirmed && p.disagreement);

  // The scale is read during extraction. Until that finishes there is simply no answer
  // yet — and saying "no scale could be read from this drawing" while the job is still
  // running is not the same claim at all. It reads as a failure, on a sheet that prints
  // its scale in plain sight.
  if (extracting && unconfirmed.length > 0) {
    return (
      <div className="flex items-center gap-2 border-b border-zinc-800 bg-zinc-900/60 px-4 py-1.5 text-xs text-zinc-400">
        <span className="text-zinc-500">⏳</span>
        Reading the sheet scale from the drawing…
      </div>
    );
  }

  // Confirmed — either the drawing proved its own scale, or the operator set it. Shown,
  // not demanded: it stays editable, because the operator owns the number.
  if (unconfirmed.length === 0 && disagreeing.length === 0) {
    const p = scale.pages[0];
    const s = p?.scale;
    return (
      <div className="flex items-center gap-2 border-b border-zinc-800 bg-zinc-900/60 px-4 py-1.5 text-xs">
        <span className="text-emerald-400">✓</span>
        <span className="text-zinc-400">
          Scale{" "}
          <span className="font-medium text-zinc-200">{s ? formatScale(s) : "—"}</span>
          {p?.note ? ` — ${p.note}` : ""}. Sizes are real mm.
        </span>
        {!locked && (
          <button
            onClick={() => setOverriding(true)}
            className="ml-1 text-zinc-500 underline hover:text-zinc-300"
          >
            change
          </button>
        )}
        {overriding && !locked && (
          <span className="flex items-center gap-1">
            <span className="text-zinc-500">1:</span>
            <input
              type="number"
              step="any"
              min="0"
              autoFocus
              value={entry[p.page_id] ?? ""}
              onChange={(e) =>
                setEntry((prev) => ({ ...prev, [p.page_id]: e.target.value }))
              }
              className="w-20 rounded border border-zinc-700 bg-zinc-900 px-1.5 py-0.5"
            />
            <button
              disabled={busy || !Number(entry[p.page_id])}
              onClick={() => {
                onSetScale(p.page_id, Number(entry[p.page_id]));
                setOverriding(false);
              }}
              className="rounded bg-zinc-700 px-2 py-0.5 hover:bg-zinc-600 disabled:opacity-40"
            >
              Apply
            </button>
          </span>
        )}
      </div>
    );
  }

  return (
    <div className="border-b border-amber-900 bg-amber-950/50 px-4 py-2 text-xs">
      {/* the operator has set a scale the drawing itself contradicts — this is a typo
          until proven otherwise, and it is the one error that reaches the cutting machine */}
      {disagreeing.map((p) => (
        <div
          key={`d-${p.page_id}`}
          className="mb-1.5 rounded border border-red-700 bg-red-950/60 px-2 py-1.5 text-red-200"
        >
          <span className="font-semibold">⚠ The drawing disagrees with you.</span>{" "}
          {p.disagreement} Fix it, or the parts will be cut at the wrong size.
        </div>
      ))}

      {unconfirmed.length > 0 && (
        <div className="mb-1 font-medium text-amber-300">
          ⚠ Confirm the sheet scale — you cannot finalize until you do
        </div>
      )}

      {unconfirmed.map((p) => (
        <div key={p.page_id} className="flex flex-wrap items-center gap-2 py-0.5">
          <span className="text-amber-200/80">
            {scale.pages.length > 1 ? `Page ${p.page_index + 1}: ` : ""}
            {p.detected
              ? `the drawing reads as ${formatScale(p.detected)}`
              : "couldn't read a scale — no printed “Scale N:M” and no dimension line to measure one from"}
            {p.note ? ` — ${p.note}` : ""}
          </span>
          {!locked && (
            <span className="flex items-center gap-1">
              <span className="text-zinc-500">set 1:</span>
              <input
                type="number"
                step="any"
                min="0"
                placeholder="5"
                value={entry[p.page_id] ?? ""}
                onChange={(e) =>
                  setEntry((prev) => ({ ...prev, [p.page_id]: e.target.value }))
                }
                className="w-24 rounded border border-zinc-700 bg-zinc-900 px-1.5 py-0.5"
              />
              <button
                disabled={busy || !Number(entry[p.page_id])}
                onClick={() => onSetScale(p.page_id, Number(entry[p.page_id]))}
                className="rounded bg-amber-700 px-2 py-0.5 font-medium hover:bg-amber-600 disabled:opacity-40"
              >
                Confirm
              </button>
              <span className="text-zinc-600">(a 2:1 magnified sheet is 0.5)</span>
            </span>
          )}
        </div>
      ))}
    </div>
  );
}
