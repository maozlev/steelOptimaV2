import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { DocumentOut, ProjectKind, ProjectListOut } from "../api/types";

const KIND_META: Record<ProjectKind, { icon: string; label: string; hint: string }> = {
  tables: {
    icon: "🧾",
    label: "Material tables",
    hint: "Find BOM tables → summary, bid, orders",
  },
  cutouts: {
    icon: "📐",
    label: "Holes & shapes",
    hint: "Find cutouts on drawings → cutout BOM",
  },
};

export default function ProjectsView({
  onOpen,
  onMergedSummary,
  onCutoutBom,
}: {
  onOpen: (projectId: number) => void;
  onMergedSummary: () => void;
  onCutoutBom: () => void;
}) {
  const [projects, setProjects] = useState<ProjectListOut[]>([]);
  // documents from before the project hierarchy — they need adopting
  const [orphans, setOrphans] = useState<DocumentOut[]>([]);
  const [name, setName] = useState("");
  const [kind, setKind] = useState<ProjectKind>("tables");
  const [error, setError] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<number | null>(null);

  const refresh = () => {
    api.listProjects().then(setProjects).catch(() => {});
    api
      .listDocuments()
      .then((docs) => setOrphans(docs.filter((d) => d.project_id == null)))
      .catch(() => {});
  };

  useEffect(() => {
    refresh();
  }, []);

  async function adopt(docId: number, projectId: number) {
    setError(null);
    try {
      await api.moveDocument(docId, projectId);
      refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function create() {
    const trimmed = name.trim();
    if (!trimmed) return;
    setError(null);
    try {
      const p = await api.createProject(trimmed, kind);
      setName("");
      onOpen(p.id);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function remove(id: number) {
    setError(null);
    setConfirmDelete(null);
    try {
      await api.deleteProject(id);
      refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <div className="mx-auto flex h-full max-w-3xl flex-col gap-6 p-8">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Projects</h1>
          <p className="text-sm text-zinc-400">
            Material tables, bids &amp; orders per tender
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={onMergedSummary}
            className="rounded bg-zinc-800 px-3 py-1.5 text-sm hover:bg-zinc-700"
          >
            📋 Merged summary
          </button>
          <button
            onClick={onCutoutBom}
            className="rounded bg-zinc-800 px-3 py-1.5 text-sm hover:bg-zinc-700"
            title="Combined cutout BOM across every approved drawing"
          >
            🔩 Cutout BOM
          </button>
        </div>
      </header>

      <div className="flex flex-col gap-2">
        <div className="flex gap-2">
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && create()}
            placeholder="New project name…"
            className="flex-1 rounded border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm outline-none focus:border-emerald-600"
          />
          <button
            onClick={create}
            disabled={!name.trim()}
            className="rounded bg-emerald-700 px-4 py-2 text-sm font-medium hover:bg-emerald-600 disabled:opacity-40"
          >
            Create
          </button>
        </div>
        {/* the user decides what this project scans for — a table scanner
            pointed at shape drawings invents tables out of title blocks */}
        <div className="flex gap-2">
          {(Object.keys(KIND_META) as ProjectKind[]).map((k) => (
            <button
              key={k}
              onClick={() => setKind(k)}
              className={`flex-1 rounded border px-3 py-2 text-left text-sm transition-colors ${
                kind === k
                  ? "border-emerald-600 bg-emerald-950/40"
                  : "border-zinc-700 bg-zinc-900 text-zinc-400 hover:border-zinc-500"
              }`}
            >
              <span className="font-medium">
                {KIND_META[k].icon} {KIND_META[k].label}
              </span>
              <span className="mt-0.5 block text-xs text-zinc-500">
                {KIND_META[k].hint}
              </span>
            </button>
          ))}
        </div>
      </div>

      {error && (
        <div className="rounded border border-red-800 bg-red-950 px-3 py-2 text-sm text-red-300">
          {error}
        </div>
      )}

      <div className="flex-1 overflow-auto">
        {projects.length === 0 ? (
          <p className="mt-8 text-center text-sm text-zinc-500">
            No projects yet — create one and drop its PDFs in.
          </p>
        ) : (
          <ul className="divide-y divide-zinc-800 rounded border border-zinc-800">
            {projects.map((p) => (
              <li key={p.id} className="flex items-center">
                <button
                  onClick={() => onOpen(p.id)}
                  className="flex flex-1 items-center justify-between px-4 py-3 text-left hover:bg-zinc-900"
                >
                  <div>
                    <div className="font-medium">
                      {KIND_META[p.kind]?.icon ?? "🧾"} {p.name}
                    </div>
                    <div className="text-xs text-zinc-500">
                      {KIND_META[p.kind]?.label ?? "Material tables"} ·{" "}
                      {p.document_count} document{p.document_count === 1 ? "" : "s"}
                      {p.kind !== "cutouts" && (
                        <>
                          {" "}· {p.table_count} table{p.table_count === 1 ? "" : "s"}
                        </>
                      )}{" "}
                      · {new Date(p.created_at).toLocaleDateString()}
                    </div>
                  </div>
                  <div className="flex items-center gap-3">
                    {p.needs_review_rows > 0 && (
                      <span className="rounded bg-amber-900/60 px-2 py-0.5 text-xs font-medium text-amber-300">
                        {p.needs_review_rows} to review
                      </span>
                    )}
                    <span className="text-zinc-500">→</span>
                  </div>
                </button>
                {confirmDelete === p.id ? (
                  <div className="flex items-center gap-1 pr-3">
                    <span className="text-xs text-zinc-400">Delete?</span>
                    <button
                      onClick={() => remove(p.id)}
                      className="rounded bg-red-800 px-2 py-1 text-xs font-medium hover:bg-red-700"
                    >
                      Yes
                    </button>
                    <button
                      onClick={() => setConfirmDelete(null)}
                      className="rounded bg-zinc-800 px-2 py-1 text-xs hover:bg-zinc-700"
                    >
                      No
                    </button>
                  </div>
                ) : (
                  <button
                    onClick={() => setConfirmDelete(p.id)}
                    className="mr-3 rounded px-2 py-1.5 text-zinc-500 hover:bg-zinc-800 hover:text-red-400"
                    title="Delete project (documents survive)"
                  >
                    ✕
                  </button>
                )}
              </li>
            ))}
          </ul>
        )}

        {orphans.length > 0 && (
          <div className="mt-6">
            <div className="mb-1 text-xs font-medium uppercase tracking-wide text-amber-400">
              Unassigned documents
            </div>
            <p className="mb-2 text-xs text-zinc-500">
              Every document belongs to a project now. Move these into one to
              keep working with them.
            </p>
            <ul className="divide-y divide-zinc-800 rounded border border-amber-900/50">
              {orphans.map((d) => (
                <li
                  key={d.id}
                  className="flex items-center justify-between gap-3 px-4 py-2.5"
                >
                  <div className="min-w-0">
                    <div className="truncate text-sm font-medium">{d.filename}</div>
                    <div className="text-xs text-zinc-500">
                      {d.page_count} page{d.page_count === 1 ? "" : "s"} ·{" "}
                      {new Date(d.created_at).toLocaleDateString()}
                    </div>
                  </div>
                  <select
                    defaultValue=""
                    onChange={(e) => {
                      const pid = Number(e.target.value);
                      if (pid) void adopt(d.id, pid);
                    }}
                    disabled={projects.length === 0}
                    className="rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-xs"
                  >
                    <option value="" disabled>
                      {projects.length ? "Move to project…" : "Create a project first"}
                    </option>
                    {projects.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.name}
                      </option>
                    ))}
                  </select>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </div>
  );
}
