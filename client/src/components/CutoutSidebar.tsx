import type { CutoutOut, CutoutStatus } from "../api/types";

export const ALL_STATUSES: CutoutStatus[] = [
  "pending",
  "approved",
  "rejected",
  "edited",
];

const STATUS_CHIP: Record<string, string> = {
  pending: "bg-amber-900/60 text-amber-300",
  approved: "bg-emerald-900/60 text-emerald-300",
  rejected: "bg-red-900/60 text-red-300",
  edited: "bg-blue-900/60 text-blue-300",
};

export interface Filters {
  statuses: Set<CutoutStatus>;
  minConf: number;
}

interface Props {
  cutouts: CutoutOut[];
  filtered: CutoutOut[];
  filters: Filters;
  onFilters: (f: Filters) => void;
  selectedId: number | null;
  onSelect: (id: number | null) => void;
  escalationThreshold: number;
}

export default function CutoutSidebar({
  cutouts,
  filtered,
  filters,
  onFilters,
  selectedId,
  onSelect,
  escalationThreshold,
}: Props) {
  const toggleStatus = (s: CutoutStatus) => {
    const next = new Set(filters.statuses);
    if (next.has(s)) next.delete(s);
    else next.add(s);
    onFilters({ ...filters, statuses: next });
  };

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-zinc-800 p-3">
        <div className="mb-2 flex flex-wrap gap-1.5">
          {ALL_STATUSES.map((s) => (
            <button
              key={s}
              onClick={() => toggleStatus(s)}
              className={`rounded px-2 py-0.5 text-xs ${
                filters.statuses.has(s)
                  ? STATUS_CHIP[s]
                  : "bg-zinc-900 text-zinc-600"
              }`}
            >
              {s} {cutouts.filter((c) => c.status === s).length}
            </button>
          ))}
        </div>
        <label className="flex items-center gap-2 text-xs text-zinc-400">
          min conf
          <input
            type="range"
            min={0}
            max={1}
            step={0.05}
            value={filters.minConf}
            onChange={(e) =>
              onFilters({ ...filters, minConf: Number(e.target.value) })
            }
            className="flex-1"
          />
          {filters.minConf.toFixed(2)}
        </label>
      </div>

      <div className="flex-1 overflow-auto">
        {filtered.length === 0 && (
          <p className="p-4 text-center text-xs text-zinc-600">
            No cutouts match the filters.
          </p>
        )}
        <ul className="divide-y divide-zinc-800/60">
          {filtered.map((c) => (
            <li key={c.id}>
              <button
                onClick={() => onSelect(c.id === selectedId ? null : c.id)}
                className={`w-full px-3 py-2 text-left text-xs hover:bg-zinc-900 ${
                  c.id === selectedId ? "bg-zinc-800/80" : ""
                }`}
              >
                <div className="flex items-center justify-between">
                  <span className="font-medium text-zinc-200">
                    #{c.id} {c.kind}
                  </span>
                  <span className={`rounded px-1.5 ${STATUS_CHIP[c.status]}`}>
                    {c.status}
                  </span>
                </div>
                <div className="mt-1 flex items-center gap-2">
                  <div className="h-1.5 flex-1 overflow-hidden rounded bg-zinc-800">
                    <div
                      className={`h-full ${
                        c.confidence < escalationThreshold
                          ? "bg-amber-500"
                          : "bg-emerald-500"
                      }`}
                      style={{ width: `${c.confidence * 100}%` }}
                    />
                  </div>
                  <span className="tabular-nums text-zinc-400">
                    {c.confidence.toFixed(2)}
                  </span>
                  <span className="text-zinc-600">{c.source}</span>
                </div>
              </button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
