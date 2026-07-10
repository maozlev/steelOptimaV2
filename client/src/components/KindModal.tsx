import type { CutoutKind } from "../api/types";

const OPTIONS: { kind: CutoutKind; label: string }[] = [
  { kind: "hole", label: "Hole" },
  { kind: "slot", label: "Slot" },
  { kind: "notch", label: "Notch" },
  { kind: "freeform", label: "Freeform / custom" },
];

export default function KindModal({
  onPick,
  onCancel,
}: {
  onPick: (kind: CutoutKind) => void;
  onCancel: () => void;
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-zinc-950/80">
      <div className="w-72 rounded-lg border border-zinc-700 bg-zinc-900 p-4">
        <h2 className="mb-3 text-sm font-medium text-zinc-200">
          Classify the drawn shape
        </h2>
        <div className="flex flex-col gap-2">
          {OPTIONS.map((o) => (
            <button
              key={o.kind}
              onClick={() => onPick(o.kind)}
              className="rounded bg-zinc-800 px-3 py-2 text-left text-sm hover:bg-zinc-700"
            >
              {o.label}
            </button>
          ))}
        </div>
        <button
          onClick={onCancel}
          className="mt-3 w-full rounded px-3 py-1.5 text-xs text-zinc-500 hover:bg-zinc-800 hover:text-zinc-300"
        >
          Cancel
        </button>
      </div>
    </div>
  );
}
