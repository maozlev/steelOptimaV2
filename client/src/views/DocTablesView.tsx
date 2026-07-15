import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { DocumentDetailOut, MaterialTableOut } from "../api/types";

const KIND_STYLE: Record<string, string> = {
  materials: "bg-emerald-900/60 text-emerald-300",
  coordinates: "bg-sky-900/60 text-sky-300",
  other: "bg-zinc-800 text-zinc-400",
  unknown: "bg-amber-900/60 text-amber-300",
};

/** The tables of ONE document: review, approve, rescan. The drawing itself
 * (scan page + holes) lives in the workspace — one click away. */
export default function DocTablesView({
  docId,
  onBack,
  onOpenTable,
  onOpenDrawing,
}: {
  docId: number;
  onBack: () => void;
  onOpenTable: (tableId: number) => void;
  onOpenDrawing: () => void;
}) {
  const [doc, setDoc] = useState<DocumentDetailOut | null>(null);
  const [tables, setTables] = useState<MaterialTableOut[]>([]);
  const [scanning, setScanning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pollRef = useRef<number | null>(null);

  const refresh = useCallback(
    () =>
      Promise.all([api.getDocument(docId), api.listDocumentTables(docId)])
        .then(([d, t]) => {
          setDoc(d);
          setTables(t);
        })
        .catch((e) => setError(e.message)),
    [docId],
  );

  useEffect(() => {
    void refresh();
    return () => {
      if (pollRef.current) window.clearInterval(pollRef.current);
    };
  }, [refresh]);

  const rescan = useCallback(async () => {
    setError(null);
    setScanning(true);
    try {
      const job = await api.startTableJob(docId);
      pollRef.current = window.setInterval(async () => {
        const j = await api.getJob(job.id).catch(() => null);
        if (j && (j.status === "done" || j.status === "failed")) {
          if (pollRef.current) window.clearInterval(pollRef.current);
          setScanning(false);
          if (j.status === "failed") setError(j.error ?? "scan failed");
          void refresh();
        }
      }, 1500);
    } catch (e) {
      setScanning(false);
      setError((e as Error).message);
    }
  }, [docId, refresh]);

  if (!doc) {
    return <div className="p-8 text-sm text-zinc-500">{error ?? "Loading…"}</div>;
  }

  return (
    <div className="mx-auto flex h-full max-w-4xl flex-col gap-4 p-8">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">{doc.filename}</h1>
          <p className="text-sm text-zinc-400">
            Material tables · {tables.length} found
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={rescan}
            disabled={scanning}
            className="rounded bg-zinc-800 px-3 py-1.5 text-sm hover:bg-zinc-700 disabled:opacity-40"
          >
            {scanning ? "Scanning…" : "↻ Rescan tables"}
          </button>
          <button
            onClick={onOpenDrawing}
            className="rounded bg-zinc-800 px-3 py-1.5 text-sm hover:bg-zinc-700"
            title="Open the drawing itself: scanned page, holes and BOM"
          >
            📐 Drawing &amp; holes
          </button>
          <button
            onClick={onBack}
            className="rounded bg-zinc-800 px-3 py-1.5 text-sm hover:bg-zinc-700"
          >
            ← Project
          </button>
        </div>
      </header>

      {error && (
        <div className="rounded border border-red-800 bg-red-950 px-3 py-2 text-sm text-red-300">
          {error}
        </div>
      )}

      <div className="flex-1 overflow-auto">
        {tables.length === 0 ? (
          <p className="mt-8 text-center text-sm text-zinc-500">
            {scanning
              ? "Scanning for material tables…"
              : "No tables detected in this document — rescan, or open the drawing instead."}
          </p>
        ) : (
          <ul className="divide-y divide-zinc-800 rounded border border-zinc-800">
            {tables.map((t) => (
              <li key={t.id} className="flex items-center">
                <button
                  onClick={() => onOpenTable(t.id)}
                  className="flex flex-1 items-center justify-between px-4 py-2.5 text-left hover:bg-zinc-900"
                >
                  <div>
                    <span className="font-medium">{t.title || `Table #${t.id}`}</span>
                    <span className="ml-2 text-xs text-zinc-500">
                      {t.n_rows}×{t.n_cols}
                    </span>
                    <span
                      className={`ml-2 rounded px-1.5 py-0.5 text-[10px] ${KIND_STYLE[t.kind]}`}
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
        )}
      </div>
    </div>
  );
}
