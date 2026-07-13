import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import { getSummaryIncludes, loadHiddenKeys, setSummaryIncludes } from "../api/bom";
import { formatLength } from "../components/BomPanel";
import type { CropIn, DocumentBom, DocumentDetailOut, DocumentOut, HealthOut } from "../api/types";
import { track } from "../telemetry";
import HealthBadge from "../components/HealthBadge";
import IngestionPreview from "../components/IngestionPreview";
import UploadDropzone from "../components/UploadDropzone";

interface HoverCard {
  docId: number;
  rect: DOMRect;
}

function BomTooltip({ doc, bom }: { doc: DocumentOut; bom: DocumentBom | undefined }) {
  if (!bom) {
    return <div className="py-3 text-center text-xs text-zinc-500">Loading…</div>;
  }
  const hidden = loadHiddenKeys(doc.id);
  const rows = bom.rows.filter((r) => !hidden.has(r.key) && r.qty > 0);
  if (rows.length === 0) {
    return <div className="py-3 text-center text-xs text-zinc-500">No cutouts yet</div>;
  }
  const cut = rows.reduce((s, r) => s + r.cut_length_total_mm, 0);
  return (
    <table className="w-full text-xs">
      <thead>
        <tr className="text-left text-zinc-500">
          <th className="pb-1 pr-3 font-normal">Shape</th>
          <th className="pb-1 pr-3 font-normal">Dimensions</th>
          <th className="pb-1 pr-3 text-right font-normal">Qty</th>
          <th className="pb-1 text-right font-normal">Cut</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.key} className="border-t border-zinc-800">
            <td className="py-0.5 pr-3 font-medium text-zinc-200">{r.shape_label}</td>
            <td className="py-0.5 pr-3 text-zinc-400">{r.dims}</td>
            <td className="py-0.5 pr-3 text-right tabular-nums text-zinc-200">{r.qty}×</td>
            <td className="py-0.5 text-right tabular-nums text-zinc-400">
              {formatLength(r.cut_length_total_mm)}
            </td>
          </tr>
        ))}
      </tbody>
      <tfoot>
        <tr className="border-t border-zinc-700 font-medium text-zinc-200">
          <td className="pt-1 pr-3" colSpan={2}>
            Total
          </td>
          <td className="pt-1 pr-3 text-right tabular-nums">
            {rows.reduce((s, r) => s + r.qty, 0)}×
          </td>
          <td className="pt-1 text-right tabular-nums text-emerald-300">
            {formatLength(cut)}
          </td>
        </tr>
      </tfoot>
    </table>
  );
}

export default function DocumentsView({
  onOpen,
  onSummary,
  onProjects,
}: {
  onOpen: (docId: number, autoRun?: boolean) => void;
  onSummary: () => void;
  onProjects: () => void;
}) {
  const [docs, setDocs] = useState<DocumentOut[]>([]);
  const [health, setHealth] = useState<HealthOut | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [previewDoc, setPreviewDoc] = useState<DocumentDetailOut | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState<number | null>(null);
  const [hoverCard, setHoverCard] = useState<HoverCard | null>(null);
  const [bomCache, setBomCache] = useState<Map<number, DocumentBom>>(new Map());
  const [summaryIncludes, setSummaryIncludesState] = useState<Set<number>>(
    () => getSummaryIncludes() ?? new Set(),
  );
  const hoverTimer = useRef<number | null>(null);

  const refresh = () => api.listDocuments().then(setDocs).catch(() => {});

  useEffect(() => {
    refresh();
    api.health().then(setHealth).catch(() => setHealth(null));
    track("documents_viewed");
  }, []);

  function toggleSummaryInclude(docId: number, e: React.MouseEvent) {
    e.stopPropagation();
    setSummaryIncludesState((prev) => {
      const next = new Set(prev);
      if (next.has(docId)) next.delete(docId);
      else next.add(docId);
      setSummaryIncludes(next);
      return next;
    });
  }

  function onRowMouseEnter(e: React.MouseEvent<HTMLLIElement>, docId: number) {
    const rect = e.currentTarget.getBoundingClientRect();
    if (hoverTimer.current) window.clearTimeout(hoverTimer.current);
    hoverTimer.current = window.setTimeout(() => {
      setHoverCard({ docId, rect });
      if (!bomCache.has(docId)) {
        api
          .getDocumentBom(docId)
          .then((b) => setBomCache((prev) => new Map([...prev, [docId, b]])))
          .catch(() => {});
      }
    }, 400);
  }

  function onRowMouseLeave() {
    if (hoverTimer.current) window.clearTimeout(hoverTimer.current);
    setHoverCard(null);
  }

  async function upload(file: File) {
    setError(null);
    try {
      const doc = await api.uploadDocument(file);
      track("document_uploaded", doc.id);
      refresh();
      setPreviewDoc(doc);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function deleteDoc(id: number) {
    setError(null);
    setConfirmDelete(null);
    if (hoverCard?.docId === id) setHoverCard(null);
    setBomCache((prev) => {
      const next = new Map(prev);
      next.delete(id);
      return next;
    });
    try {
      await api.deleteDocument(id);
      refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function confirmCrop(crop: CropIn | null) {
    if (!previewDoc) return;
    setBusy(true);
    setError(null);
    try {
      if (crop) await api.cropDocument(previewDoc.id, crop);
      const id = previewDoc.id;
      setPreviewDoc(null);
      onOpen(id, true);
    } catch (e) {
      setError((e as Error).message);
      setPreviewDoc(null);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto flex h-full max-w-3xl flex-col gap-6 p-8">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">SteelOptima</h1>
          <p className="text-sm text-zinc-400">
            Cutout extraction &amp; validation workspace
          </p>
        </div>
        <div className="flex items-center gap-3">
          <button
            onClick={onProjects}
            className="rounded bg-zinc-800 px-3 py-1.5 text-sm hover:bg-zinc-700"
          >
            🗂 Projects
          </button>
          <button
            onClick={onSummary}
            className="rounded bg-zinc-800 px-3 py-1.5 text-sm hover:bg-zinc-700"
          >
            📋 Summary
          </button>
          <HealthBadge health={health} />
        </div>
      </header>

      <UploadDropzone onFile={upload} />
      {error && (
        <div className="rounded border border-red-800 bg-red-950 px-3 py-2 text-sm text-red-300">
          {error}
        </div>
      )}

      <div className="flex-1 overflow-auto">
        {docs.length === 0 ? (
          <p className="mt-8 text-center text-sm text-zinc-500">
            No documents yet — drop a blueprint PDF or image above.
          </p>
        ) : (
          <ul className="divide-y divide-zinc-800 rounded border border-zinc-800">
            {docs.map((d) => (
              <li
                key={d.id}
                className="relative flex items-center"
                onMouseEnter={(e) => onRowMouseEnter(e, d.id)}
                onMouseLeave={onRowMouseLeave}
              >
                <button
                  onClick={() => onOpen(d.id)}
                  className="flex flex-1 items-center justify-between px-4 py-3 text-left hover:bg-zinc-900"
                >
                  <div>
                    <div className="font-medium">{d.filename}</div>
                    <div className="text-xs text-zinc-500">
                      {d.page_count} page{d.page_count === 1 ? "" : "s"} ·{" "}
                      {new Date(d.created_at).toLocaleString()}
                    </div>
                  </div>
                  <div className="flex items-center gap-3">
                    <span
                      className={`rounded px-2 py-0.5 text-xs font-medium ${
                        d.status === "approved"
                          ? "bg-emerald-900/60 text-emerald-300"
                          : "bg-amber-900/60 text-amber-300"
                      }`}
                    >
                      {d.status === "approved" ? "APPROVED" : "PENDING"}
                    </span>
                    <span className="text-zinc-500">→</span>
                  </div>
                </button>

                {/* Include-in-aggregated-summary toggle (approved docs only) */}
                {d.status === "approved" && (
                  <button
                    onClick={(e) => toggleSummaryInclude(d.id, e)}
                    title={
                      summaryIncludes.has(d.id)
                        ? "Included in summary — click to exclude"
                        : "Excluded from summary — click to include"
                    }
                    className={`mr-1 rounded px-2 py-1.5 text-sm transition-colors ${
                      summaryIncludes.has(d.id)
                        ? "text-emerald-400 hover:bg-zinc-800"
                        : "text-zinc-700 hover:bg-zinc-800 hover:text-zinc-400"
                    }`}
                  >
                    ✦
                  </button>
                )}

                {confirmDelete === d.id ? (
                  <div className="flex items-center gap-1 pr-3">
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
                  </div>
                ) : (
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      setConfirmDelete(d.id);
                    }}
                    className="mr-3 rounded px-2 py-1.5 text-zinc-500 hover:bg-zinc-800 hover:text-red-400"
                    title="Delete document"
                  >
                    ✕
                  </button>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* Hover BOM tooltip (fixed position) */}
      {hoverCard && docs.find((d) => d.id === hoverCard.docId) && (
        <div
          style={{
            position: "fixed",
            top: hoverCard.rect.top,
            left: Math.min(hoverCard.rect.right + 12, window.innerWidth - 340),
            zIndex: 50,
            width: 300,
          }}
          className="rounded-lg border border-zinc-700 bg-zinc-900 p-3 shadow-2xl"
          onMouseEnter={() => {
            if (hoverTimer.current) window.clearTimeout(hoverTimer.current);
          }}
          onMouseLeave={() => setHoverCard(null)}
        >
          <div className="mb-2 truncate text-xs font-medium text-zinc-300">
            {docs.find((d) => d.id === hoverCard.docId)?.filename}
          </div>
          <BomTooltip
            doc={docs.find((d) => d.id === hoverCard.docId)!}
            bom={bomCache.get(hoverCard.docId)}
          />
        </div>
      )}

      {previewDoc && (
        <IngestionPreview
          doc={previewDoc}
          busy={busy}
          onConfirm={confirmCrop}
          onCancel={() => setPreviewDoc(null)}
        />
      )}
    </div>
  );
}
