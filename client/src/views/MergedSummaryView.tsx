import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { ProjectListOut, ProjectSummary } from "../api/types";
import ChatPanel from "../components/ChatPanel";
import MaterialSummaryTable, {
  exportSummaryCsv,
} from "../components/MaterialSummaryTable";

export default function MergedSummaryView({ onBack }: { onBack: () => void }) {
  const [projects, setProjects] = useState<ProjectListOut[]>([]);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [summary, setSummary] = useState<ProjectSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showChat, setShowChat] = useState(false);

  useEffect(() => {
    api
      .listProjects()
      .then((list) => {
        setProjects(list);
        setSelected(new Set(list.map((p) => p.id)));
      })
      .catch((e) => setError(e.message));
  }, []);

  useEffect(() => {
    if (selected.size === 0) {
      setSummary(null);
      return;
    }
    api
      .getProjectsSummary([...selected])
      .then(setSummary)
      .catch((e) => setError(e.message));
  }, [selected]);

  function toggle(id: number) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  return (
    <div className="mx-auto flex h-full max-w-5xl flex-col gap-4 p-8">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">
            Merged materials summary
          </h1>
          <p className="text-sm text-zinc-400">
            One table across {selected.size} project{selected.size === 1 ? "" : "s"}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setShowChat((v) => !v)}
            className={`rounded px-3 py-1.5 text-sm ${
              showChat
                ? "bg-emerald-800 hover:bg-emerald-700"
                : "bg-zinc-800 hover:bg-zinc-700"
            }`}
          >
            💬 Chat
          </button>
          <button
            onClick={() =>
              summary && exportSummaryCsv(summary, "merged-materials.csv")
            }
            disabled={!summary || summary.rows.length === 0}
            className="rounded bg-zinc-800 px-3 py-1.5 text-sm hover:bg-zinc-700 disabled:opacity-40"
          >
            ⬇ CSV
          </button>
          <button
            onClick={onBack}
            className="rounded bg-zinc-800 px-3 py-1.5 text-sm hover:bg-zinc-700"
          >
            ← Projects
          </button>
        </div>
      </header>

      {error && (
        <div className="rounded border border-red-800 bg-red-950 px-3 py-2 text-sm text-red-300">
          {error}
        </div>
      )}

      <div className="flex flex-wrap gap-2">
        {projects.map((p) => (
          <label
            key={p.id}
            className={`flex cursor-pointer items-center gap-1.5 rounded border px-2.5 py-1 text-sm ${
              selected.has(p.id)
                ? "border-emerald-700 bg-emerald-950/40"
                : "border-zinc-700 bg-zinc-900 text-zinc-500"
            }`}
          >
            <input
              type="checkbox"
              checked={selected.has(p.id)}
              onChange={() => toggle(p.id)}
              className="hidden"
            />
            {p.name}
          </label>
        ))}
      </div>

      <div className="flex min-h-0 flex-1 gap-4">
        <div className="flex-1 overflow-auto">
          {summary ? (
            <MaterialSummaryTable summary={summary} showProjects />
          ) : (
            <p className="mt-6 text-center text-sm text-zinc-500">
              Select at least one project.
            </p>
          )}
        </div>
        {showChat && (
          <aside className="flex w-96 shrink-0 flex-col rounded border border-zinc-800 bg-zinc-950">
            <ChatPanel
              scope="summary"
              scopeId={0}
              hint="Context: ALL projects + order plans (ignores the selection above)"
            />
          </aside>
        )}
      </div>
    </div>
  );
}
