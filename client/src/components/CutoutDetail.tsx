import { useState } from "react";
import type { CutoutKind, CutoutOut } from "../api/types";

const KINDS: CutoutKind[] = ["hole", "slot", "notch", "freeform"];

interface Props {
  cutout: CutoutOut;
  busy: boolean;
  onAction: (action: "approve" | "reject") => void;
  onKind: (kind: CutoutKind) => void;
  onRedraw: () => void;
  redrawing: boolean;
}

export default function CutoutDetail({
  cutout,
  busy,
  onAction,
  onKind,
  onRedraw,
  redrawing,
}: Props) {
  const [kind, setKind] = useState<CutoutKind>(cutout.kind);
  const dims = cutout.measured_dims_json
    ? (JSON.parse(cutout.measured_dims_json) as Record<string, number>)
    : null;

  return (
    <div className="border-t border-zinc-800 p-3 text-xs">
      <div className="mb-2 flex items-center justify-between">
        <span className="font-semibold text-zinc-200">
          Cutout #{cutout.id} · {cutout.kind} · {cutout.source}
        </span>
        <span className="text-zinc-500">conf {cutout.confidence.toFixed(2)}</span>
      </div>

      {dims && (
        <div className="mb-2 flex flex-wrap gap-x-3 gap-y-0.5 text-zinc-400">
          {Object.entries(dims).map(([k, v]) => (
            <span key={k}>
              {k.replace("_mm", "")}: <b className="text-zinc-300">{v}mm</b>
            </span>
          ))}
          {cutout.dimension_text && (
            <span>
              annotated: <b className="text-zinc-300">{cutout.dimension_text}</b>
            </span>
          )}
        </div>
      )}

      <div className="mb-2 flex gap-2">
        <button
          disabled={busy}
          onClick={() => onAction("approve")}
          className="flex-1 rounded bg-emerald-700 py-1.5 font-medium hover:bg-emerald-600 disabled:opacity-50"
        >
          Approve
        </button>
        <button
          disabled={busy}
          onClick={() => onAction("reject")}
          className="flex-1 rounded bg-red-800 py-1.5 font-medium hover:bg-red-700 disabled:opacity-50"
        >
          Reject
        </button>
      </div>

      <div className="flex items-center gap-2">
        <select
          value={kind}
          onChange={(e) => setKind(e.target.value as CutoutKind)}
          className="flex-1 rounded border border-zinc-700 bg-zinc-900 px-2 py-1"
        >
          {KINDS.map((k) => (
            <option key={k} value={k}>
              {k}
            </option>
          ))}
        </select>
        <button
          disabled={busy || kind === cutout.kind}
          onClick={() => onKind(kind)}
          className="rounded bg-blue-800 px-3 py-1 hover:bg-blue-700 disabled:opacity-50"
        >
          Set kind
        </button>
        <button
          disabled={busy}
          onClick={onRedraw}
          className={`rounded px-3 py-1 ${
            redrawing
              ? "bg-cyan-600"
              : "bg-zinc-800 hover:bg-zinc-700"
          }`}
        >
          {redrawing ? "Draw on page…" : "Redraw"}
        </button>
      </div>
    </div>
  );
}
