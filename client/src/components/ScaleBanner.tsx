import { useState } from "react";
import type { ScaleStatus } from "../api/types";

/** "1:5" for a reduction, "2:1" for a magnified sheet. */
export function formatScale(scale: number): string {
  return scale >= 1 ? `1:${+scale.toFixed(3)}` : `${+(1 / scale).toFixed(3)}:1`;
}

/**
 * Every dimension in the BOM is measured in PAPER mm and multiplied by the sheet scale.
 * Where that scale is unknown or unverified, the numbers are the size of ink on a page,
 * not the size of a part — and the operator has to be told before they cut anything.
 */
export default function ScaleBanner({
  scale,
  locked,
  busy,
  onSetScale,
}: {
  scale: ScaleStatus;
  locked: boolean;
  busy: boolean;
  onSetScale: (pageId: number, scale: number) => void;
}) {
  const [entry, setEntry] = useState<Record<number, string>>({});
  const unverified = scale.pages.filter((p) => !p.scale || !p.confident);

  if (unverified.length === 0) {
    const s = scale.pages[0]?.scale;
    return (
      <div className="flex items-center gap-2 border-b border-zinc-800 bg-zinc-900/60 px-4 py-1.5 text-xs">
        <span className="text-emerald-400">✓</span>
        <span className="text-zinc-400">
          Scale <span className="font-medium text-zinc-200">{s ? formatScale(s) : "—"}</span>{" "}
          — confirmed against the drawing's own dimensions. Sizes are real mm.
        </span>
      </div>
    );
  }

  return (
    <div className="border-b border-amber-900 bg-amber-950/50 px-4 py-2 text-xs">
      <div className="mb-1 font-medium text-amber-300">
        ⚠ Scale not verified — these dimensions are NOT safe to cut from
      </div>
      {unverified.map((p) => (
        <div key={p.page_id} className="flex flex-wrap items-center gap-2 py-0.5">
          <span className="text-amber-200/80">
            {scale.pages.length > 1 ? `Page ${p.page_index + 1}: ` : ""}
            {p.scale
              ? `best guess ${formatScale(p.scale)}`
              : "could not be determined"}
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
                className="w-20 rounded border border-zinc-700 bg-zinc-900 px-1.5 py-0.5"
              />
              <button
                disabled={busy || !Number(entry[p.page_id])}
                onClick={() => onSetScale(p.page_id, Number(entry[p.page_id]))}
                className="rounded bg-amber-700 px-2 py-0.5 font-medium hover:bg-amber-600 disabled:opacity-40"
              >
                Apply
              </button>
              <span className="text-zinc-600">
                (a 2:1 magnified sheet is 0.5)
              </span>
            </span>
          )}
        </div>
      ))}
    </div>
  );
}
