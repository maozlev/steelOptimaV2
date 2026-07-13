import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { ProjectDetailOut } from "../api/types";
import UploadDropzone from "../components/UploadDropzone";

const JOB_LABEL: Record<string, string> = {
  queued: "QUEUED",
  running: "SCANNING…",
  done: "SCANNED",
  failed: "FAILED",
};

export default function ProjectView({
  projectId,
  onBack,
}: {
  projectId: number;
  onBack: () => void;
}) {
  const [project, setProject] = useState<ProjectDetailOut | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState<{ done: number; total: number } | null>(
    null,
  );
  // one sequential upload queue — dropping 200 PDFs must not fire 200 parallel posts
  const queue = useRef<File[]>([]);
  const pumping = useRef(false);

  const refresh = useCallback(
    () => api.getProject(projectId).then(setProject).catch((e) => setError(e.message)),
    [projectId],
  );

  useEffect(() => {
    refresh();
  }, [refresh]);

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
      <div className="p-8 text-sm text-zinc-500">
        {error ?? "Loading project…"}
      </div>
    );
  }

  return (
    <div className="mx-auto flex h-full max-w-3xl flex-col gap-6 p-8">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">{project.name}</h1>
          <p className="text-sm text-zinc-400">
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

      <UploadDropzone onFile={enqueue} multiple />

      {uploading && (
        <div className="rounded border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm text-zinc-300">
          Uploading {uploading.done + 1} / {uploading.total}…
        </div>
      )}
      {error && (
        <div className="rounded border border-red-800 bg-red-950 px-3 py-2 text-sm text-red-300">
          {error}
        </div>
      )}

      <div className="flex-1 overflow-auto">
        {project.documents.length === 0 ? (
          <p className="mt-8 text-center text-sm text-zinc-500">
            No documents yet — drop this tender's PDFs above.
          </p>
        ) : (
          <ul className="divide-y divide-zinc-800 rounded border border-zinc-800">
            {project.documents.map((d) => (
              <li key={d.id} className="flex items-center justify-between px-4 py-3">
                <div>
                  <div className="font-medium">{d.filename}</div>
                  <div className="text-xs text-zinc-500">
                    {d.page_count} page{d.page_count === 1 ? "" : "s"} ·{" "}
                    {new Date(d.created_at).toLocaleString()}
                  </div>
                </div>
                <div className="flex items-center gap-2">
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
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
