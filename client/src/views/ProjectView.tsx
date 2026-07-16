import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type {
  MaterialTableOut,
  ProjectDetailOut,
  ProjectSummary,
} from "../api/types";
import BidPanel from "../components/BidPanel";
import ChatPanel from "../components/ChatPanel";
import MaterialSummaryTable, {
  exportSummaryCsv,
} from "../components/MaterialSummaryTable";
import OrdersPanel from "../components/OrdersPanel";
import QueuePanel from "../components/QueuePanel";
import UploadDropzone from "../components/UploadDropzone";

const JOB_LABEL: Record<string, string> = {
  queued: "QUEUED",
  running: "SCANNING…",
  done: "SCANNED",
  failed: "FAILED",
};

type Tab = "documents" | "tables" | "summary" | "bid" | "orders" | "chat";

const KIND_STYLE: Record<string, string> = {
  materials: "bg-emerald-900/60 text-emerald-300",
  coordinates: "bg-sky-900/60 text-sky-300",
  other: "bg-zinc-800 text-zinc-400",
  unknown: "bg-amber-900/60 text-amber-300",
};

export default function ProjectView({
  projectId,
  onBack,
  onOpenTable,
  onOpenDocTables,
  onOpenDrawing,
}: {
  projectId: number;
  onBack: () => void;
  onOpenTable: (tableId: number) => void;
  onOpenDocTables: (docId: number) => void;
  onOpenDrawing: (docId: number) => void;
}) {
  const [project, setProject] = useState<ProjectDetailOut | null>(null);
  const [tab, setTab] = useState<Tab>("documents");
  const [tables, setTables] = useState<Map<number, MaterialTableOut[]>>(new Map());
  const [summary, setSummary] = useState<ProjectSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState<{ done: number; total: number } | null>(
    null,
  );
  const [confirmDelete, setConfirmDelete] = useState<number | null>(null);
  const queue = useRef<File[]>([]);
  const pumping = useRef(false);

  async function deleteDoc(docId: number) {
    setConfirmDelete(null);
    setError(null);
    try {
      await api.deleteDocument(docId);
      refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  const refresh = useCallback(
    () => api.getProject(projectId).then(setProject).catch((e) => setError(e.message)),
    [projectId],
  );
  useEffect(() => {
    refresh();
  }, [refresh]);

  // poll while any document is still being scanned
  const scanning = project?.documents.some((d) =>
    ["queued", "running"].includes(d.last_table_job_status ?? ""),
  );
  useEffect(() => {
    if (!scanning) return;
    const t = window.setInterval(refresh, 2500);
    return () => window.clearInterval(t);
  }, [scanning, refresh]);

  const loadTables = useCallback(async () => {
    if (!project) return;
    const entries = await Promise.all(
      project.documents.map(
        async (d) => [d.id, await api.listDocumentTables(d.id)] as const,
      ),
    );
    setTables(new Map(entries));
  }, [project]);

  const loadSummary = useCallback(
    () =>
      api.getProjectSummary(projectId).then(setSummary).catch((e) =>
        setError(e.message),
      ),
    [projectId],
  );

  useEffect(() => {
    if (tab === "tables") void loadTables();
    if (tab === "summary" || tab === "orders" || tab === "bid") void loadSummary();
  }, [tab, loadTables, loadSummary]);

  const pump = useCallback(async () => {
    if (pumping.current) return;
    pumping.current = true;
    let done = 0;
    const failures: string[] = [];
    while (queue.current.length > 0) {
      const total = done + queue.current.length;
      setUploading({ done, total });
      const file = queue.current.shift()!;
      try {
        await api.uploadProjectDocument(projectId, file);
      } catch (e) {
        failures.push(`${file.name}: ${(e as Error).message}`);
      }
      done += 1;
      refresh();
    }
    pumping.current = false;
    setUploading(null);
    setError(failures.length ? failures.join(" · ") : null);
  }, [projectId, refresh]);

  const enqueue = useCallback(
    (file: File) => {
      queue.current.push(file);
      void pump();
    },
    [pump],
  );

  if (!project) {
    return (
      <div className="p-8 text-sm text-zinc-500">{error ?? "Loading project…"}</div>
    );
  }

  const tabButton = (t: Tab, label: string) => (
    <button
      key={t}
      onClick={() => setTab(t)}
      className={`rounded px-3 py-1.5 text-sm ${
        tab === t ? "bg-zinc-700 font-medium" : "bg-zinc-900 hover:bg-zinc-800"
      }`}
    >
      {label}
    </button>
  );

  const isCutouts = project.kind === "cutouts";

  return (
    <div className="mx-auto flex h-full max-w-5xl flex-col gap-4 p-8">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">
            {isCutouts ? "📐" : "🧾"} {project.name}
          </h1>
          <p className="text-sm text-zinc-400">
            {isCutouts ? "Holes & shapes" : "Material tables"} ·{" "}
            {project.documents.length} document
            {project.documents.length === 1 ? "" : "s"}
            {project.note ? ` · ${project.note}` : ""}
          </p>
        </div>
        <button
          onClick={onBack}
          className="rounded bg-zinc-800 px-3 py-1.5 text-sm hover:bg-zinc-700"
        >
          ← Projects
        </button>
      </header>

      {/* a cutouts project has no material tables — its tabs would be empty */}
      <nav className="flex gap-2">
        {tabButton("documents", "📄 Documents")}
        {!isCutouts && tabButton("tables", "🧾 Tables")}
        {!isCutouts && tabButton("summary", "📋 Summary")}
        {!isCutouts && tabButton("bid", "💰 Bid")}
        {!isCutouts && tabButton("orders", "✂ Orders")}
        {!isCutouts && tabButton("chat", "💬 Chat")}
      </nav>

      {error && (
        <div className="rounded border border-red-800 bg-red-950 px-3 py-2 text-sm text-red-300">
          {error}
        </div>
      )}

      <div className="flex-1 overflow-auto">
        {tab === "documents" && (
          <div className="flex flex-col gap-4">
            <UploadDropzone onFile={enqueue} multiple />
            <QueuePanel
              projectId={projectId}
              uploading={uploading}
              onChanged={refresh}
            />
            {project.documents.length === 0 ? (
              <p className="mt-6 text-center text-sm text-zinc-500">
                No documents yet — drop this tender's PDFs above.{" "}
                {isCutouts ? "Hole & shape" : "Table"} scanning starts
                automatically.
              </p>
            ) : (
              <ul className="divide-y divide-zinc-800 rounded border border-zinc-800">
                {project.documents.map((d) => (
                  <li
                    key={d.id}
                    className="flex items-center justify-between px-4 py-3"
                  >
                    <button
                      onClick={() =>
                        !isCutouts && d.table_count > 0
                          ? onOpenDocTables(d.id)
                          : onOpenDrawing(d.id)
                      }
                      className="-mx-2 flex-1 rounded px-2 py-1 text-left hover:bg-zinc-900"
                      title={
                        !isCutouts && d.table_count > 0
                          ? "Open this document's material tables"
                          : "Open the drawing: scanned page and holes"
                      }
                    >
                      <div className="font-medium">{d.filename}</div>
                      <div className="text-xs text-zinc-500">
                        {d.page_count} page{d.page_count === 1 ? "" : "s"} ·{" "}
                        {new Date(d.created_at).toLocaleString()}
                      </div>
                    </button>
                    <div className="flex items-center gap-2">
                      {isCutouts ? (
                        <>
                          {d.pending_cutouts > 0 && (
                            <span className="rounded bg-amber-900/60 px-2 py-0.5 text-xs font-medium text-amber-300">
                              {d.pending_cutouts} to review
                            </span>
                          )}
                          {d.cutout_count > 0 && (
                            <span className="rounded bg-zinc-800 px-2 py-0.5 text-xs text-zinc-300">
                              {d.cutout_count} cutout{d.cutout_count === 1 ? "" : "s"}
                            </span>
                          )}
                        </>
                      ) : (
                        <>
                          {d.needs_review_rows > 0 && (
                            <span className="rounded bg-amber-900/60 px-2 py-0.5 text-xs font-medium text-amber-300">
                              {d.needs_review_rows} to review
                            </span>
                          )}
                          {d.table_count > 0 && (
                            <span className="rounded bg-zinc-800 px-2 py-0.5 text-xs text-zinc-300">
                              {d.table_count} table{d.table_count === 1 ? "" : "s"}
                            </span>
                          )}
                        </>
                      )}
                      {d.last_table_job_status && (
                        <span
                          className={`rounded px-2 py-0.5 text-xs font-medium ${
                            d.last_table_job_status === "failed"
                              ? "bg-red-900/60 text-red-300"
                              : d.last_table_job_status === "done"
                                ? "bg-emerald-900/60 text-emerald-300"
                                : "bg-sky-900/60 text-sky-300"
                          }`}
                        >
                          {JOB_LABEL[d.last_table_job_status]}
                        </span>
                      )}
                      <button
                        onClick={() =>
                          (isCutouts
                            ? api.startJob(d.id, true)
                            : api.startTableJob(d.id)
                          )
                            .then(refresh)
                            .catch((e) => setError(e.message))
                        }
                        className="rounded bg-zinc-800 px-2 py-1 text-xs hover:bg-zinc-700"
                        title={
                          isCutouts
                            ? "(Re)scan for holes & shapes"
                            : "(Re)scan for material tables"
                        }
                      >
                        ↻ scan
                      </button>
                      <button
                        onClick={() => onOpenDrawing(d.id)}
                        className="rounded bg-zinc-800 px-2 py-1 text-xs hover:bg-zinc-700"
                        title="Open the drawing: scanned page, holes and BOM"
                      >
                        📐
                      </button>
                      {confirmDelete === d.id ? (
                        <span className="flex items-center gap-1">
                          <span className="text-xs text-zinc-400">Delete?</span>
                          <button
                            onClick={() => deleteDoc(d.id)}
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
                        </span>
                      ) : (
                        <button
                          onClick={() => setConfirmDelete(d.id)}
                          className="rounded px-2 py-1 text-xs text-zinc-500 hover:bg-zinc-800 hover:text-red-400"
                          title="Delete this document from the project"
                        >
                          ✕
                        </button>
                      )}
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}

        {tab === "tables" && (
          <div className="flex flex-col gap-3">
            {[...tables.entries()].map(([docId, docTables]) => {
              const doc = project.documents.find((d) => d.id === docId);
              if (!docTables.length) return null;
              return (
                <div key={docId}>
                  <div className="mb-1 text-xs font-medium text-zinc-400">
                    {doc?.filename}
                  </div>
                  <ul className="divide-y divide-zinc-800 rounded border border-zinc-800">
                    {docTables.map((t) => (
                      <li key={t.id} className="flex items-center">
                        <button
                          onClick={() => onOpenTable(t.id)}
                          className="flex flex-1 items-center justify-between px-4 py-2.5 text-left hover:bg-zinc-900"
                        >
                          <div>
                            <span className="font-medium">
                              {t.title || `Table #${t.id}`}
                            </span>
                            <span className="ml-2 text-xs text-zinc-500">
                              {t.n_rows}×{t.n_cols}
                            </span>
                            <span
                              className={`ml-2 rounded px-1.5 py-0.5 text-[10px] ${
                                KIND_STYLE[t.kind]
                              }`}
                            >
                              {t.kind}
                            </span>
                          </div>
                          <div className="flex items-center gap-2 text-xs">
                            {t.needs_review_rows > 0 && (
                              <span className="rounded bg-amber-900/60 px-2 py-0.5 font-medium text-amber-300">
                                {t.needs_review_rows} flagged
                              </span>
                            )}
                            {t.status === "approved" && (
                              <span className="rounded bg-emerald-900/60 px-2 py-0.5 font-medium text-emerald-300">
                                APPROVED
                              </span>
                            )}
                            {t.status === "rejected" && (
                              <span className="rounded bg-zinc-800 px-2 py-0.5 text-zinc-500">
                                ignored
                              </span>
                            )}
                            <span className="text-zinc-500">→</span>
                          </div>
                        </button>
                      </li>
                    ))}
                  </ul>
                </div>
              );
            })}
            {[...tables.values()].every((v) => v.length === 0) && (
              <p className="mt-6 text-center text-sm text-zinc-500">
                No tables detected yet — scan documents first.
              </p>
            )}
          </div>
        )}

        {tab === "summary" &&
          (summary ? (
            <div>
              <div className="mb-3 flex justify-end">
                <button
                  onClick={() =>
                    exportSummaryCsv(summary, `${project.name}-materials.csv`)
                  }
                  disabled={summary.rows.length === 0}
                  className="rounded bg-zinc-800 px-3 py-1.5 text-sm hover:bg-zinc-700 disabled:opacity-40"
                >
                  ⬇ CSV
                </button>
              </div>
              <MaterialSummaryTable summary={summary} />
            </div>
          ) : (
            <p className="text-sm text-zinc-500">Loading summary…</p>
          ))}

        {tab === "bid" && <BidPanel projectId={projectId} />}

        {tab === "orders" && (
          <OrdersPanel projectId={projectId} summary={summary} />
        )}

        {tab === "chat" && (
          <div className="flex h-full flex-col rounded border border-zinc-800 bg-zinc-950">
            <ChatPanel
              scope="project"
              scopeId={projectId}
              hint="Context: this project — its documents, materials, prices and order plans"
            />
          </div>
        )}
      </div>
    </div>
  );
}
