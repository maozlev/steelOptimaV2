import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api/client";
import { addToSummaryIncludes } from "../api/bom";
import type {
  BomRow,
  ScaleStatus,
  CutoutKind,
  CutoutOut,
  DocumentDetailOut,
} from "../api/types";
import { useJobEvents } from "../api/ws";
import { polygonToWkt, rectToWkt } from "../api/wkt";
import { sessionId, track } from "../telemetry";
import BomPanel from "../components/BomPanel";
import ScaleBanner from "../components/ScaleBanner";
import CutoutDetail from "../components/CutoutDetail";
import CutoutSidebar, { ALL_STATUSES, type Filters } from "../components/CutoutSidebar";
import JobProgress from "../components/JobProgress";
import KindModal from "../components/KindModal";
import PageViewer, { type DrawMode } from "../components/PageViewer";
import SummaryPanel from "../components/SummaryPanel";

const KINDS: CutoutKind[] = ["hole", "slot", "notch", "freeform"];

export default function WorkspaceView({
  docId,
  autoRun = false,
  onBack,
}: {
  docId: number;
  autoRun?: boolean;
  onBack: () => void;
}) {
  const [doc, setDoc] = useState<DocumentDetailOut | null>(null);
  const [pageIdx, setPageIdx] = useState(0);
  // Every cutout in the document, not just the page on screen — the BOM and the
  // finalize preview cover the whole document, so they need all of them.
  const [cutouts, setCutouts] = useState<CutoutOut[]>([]);
  const [bomRows, setBomRows] = useState<BomRow[]>([]);
  const [scale, setScale] = useState<ScaleStatus>({ pages: [], trustworthy: true });
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [filters, setFilters] = useState<Filters>({
    statuses: new Set(ALL_STATUSES),
    minConf: 0.9,
  });
  const [vlm, setVlm] = useState(false);
  const [jobId, setJobId] = useState<number | null>(null);
  const [jobRunning, setJobRunning] = useState(false);
  const [drawMode, setDrawMode] = useState<DrawMode>(null);
  const [addKind, setAddKind] = useState<CutoutKind>("hole");
  const [pendingPolygon, setPendingPolygon] = useState<[number, number][] | null>(null);
  const [sidebarTab, setSidebarTab] = useState<"bom" | "list">("bom");
  const [highlightIds, setHighlightIds] = useState<number[] | null>(null);
  const [thresholds, setThresholds] = useState({
    escalation: 0.65,
    finalize: 0.9,
  });
  const [showSummary, setShowSummary] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const autoRanRef = useRef(false);

  const page = doc?.pages[pageIdx] ?? null;
  const locked = doc?.status === "approved";

  /** Cutouts and BOM rows always refresh together, so the table can never drift
   *  from the canvas. The BOM is computed server-side — see api/bom.py. */
  const refetch = useCallback(() => {
    api.listDocumentCutouts(docId).then(setCutouts).catch(() => {});
    api
      .getDocumentBom(docId)
      .then((b) => {
        setBomRows(b.rows);
        setScale(b.scale);
      })
      .catch(() => {});
  }, [docId]);

  useEffect(() => {
    api.getDocument(docId).then(setDoc).catch((e) => setError(e.message));
    api
      .getConfig()
      .then((c) =>
        setThresholds({
          escalation: c.escalation_threshold,
          finalize: c.finalize_threshold,
        }),
      )
      .catch(() => {});
    refetch();
  }, [docId, refetch]);

  useEffect(() => {
    setSelectedId(null);
    setHighlightIds(null);
    if (page) track("page_viewed", page.id);
  }, [page]);

  const events = useJobEvents(jobId, () => {
    setJobRunning(false);
    refetch();
  });

  // The canvas only ever draws the page you are looking at.
  const pageCutouts = useMemo(
    () => (page ? cutouts.filter((c) => c.page_id === page.id) : []),
    [cutouts, page],
  );
  const filtered = useMemo(
    () =>
      pageCutouts.filter(
        (c) => filters.statuses.has(c.status) && c.confidence >= filters.minConf,
      ),
    [pageCutouts, filters],
  );
  const selected = cutouts.find((c) => c.id === selectedId) ?? null;

  const runJob = useCallback(
    async (withVlm: boolean) => {
      setError(null);
      try {
        const job = await api.startJob(docId, withVlm);
        setJobId(job.id);
        setJobRunning(true);
      } catch (e) {
        setError((e as Error).message);
      }
    },
    [docId],
  );

  useEffect(() => {
    if (autoRun && doc && doc.status !== "approved" && !autoRanRef.current) {
      autoRanRef.current = true;
      runJob(false);
    }
  }, [autoRun, doc, runJob]);

  async function mutate(fn: () => Promise<CutoutOut>) {
    setBusy(true);
    setError(null);
    try {
      const updated = await fn();
      // Update in place for an immediate response, then resync so the
      // server-computed BOM rows reflect the change too.
      setCutouts((prev) => {
        const i = prev.findIndex((c) => c.id === updated.id);
        return i === -1
          ? [...prev, updated]
          : prev.map((c) => (c.id === updated.id ? updated : c));
      });
      refetch();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  function onRect(x0: number, y0: number, x1: number, y1: number) {
    const wkt = rectToWkt(x0, y0, x1, y1);
    if (drawMode === "add" && page) {
      mutate(() =>
        api.addCutout(page.id, {
          geometry_wkt: wkt,
          kind: addKind,
          session_id: sessionId,
        }),
      );
    } else if (drawMode === "edit" && selected) {
      mutate(() =>
        api.patchCutout(selected.id, {
          action: "edit",
          geometry_wkt: wkt,
          session_id: sessionId,
        }),
      );
    }
    setDrawMode(null);
  }

  function onPolygon(points: [number, number][]) {
    setPendingPolygon(points);
  }

  function addPolygonCutout(kind: CutoutKind) {
    if (!pendingPolygon || !page) return;
    const wkt = polygonToWkt(pendingPolygon);
    setPendingPolygon(null);
    setDrawMode(null);
    mutate(() =>
      api.addCutout(page.id, { geometry_wkt: wkt, kind, session_id: sessionId }),
    );
  }

  async function finalize() {
    if (!doc) return;
    setBusy(true);
    setError(null);
    try {
      const out = await api.finalizeDocument(doc.id, sessionId);
      setDoc({ ...doc, status: out.document.status });
      addToSummaryIncludes(doc.id);
      track("document_finalized", doc.id);
      refetch();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function rejectGroup(ids: number[]) {
    if (ids.length === 0) return;
    setBusy(true);
    setError(null);
    try {
      const results = await Promise.all(
        ids.map((id) => api.patchCutout(id, { action: "reject", session_id: sessionId })),
      );
      setCutouts((prev) =>
        prev.map((c) => results.find((r) => r.id === c.id) ?? c),
      );
      refetch();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function exportDoc() {
    if (!doc) return;
    try {
      const payload = await api.exportDocument(doc.id);
      const blob = new Blob([JSON.stringify(payload, null, 2)], {
        type: "application/json",
      });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `${doc.filename.replace(/\.(pdf|jpe?g|png)$/i, "")}.cutouts.json`;
      a.click();
      URL.revokeObjectURL(a.href);
      track("export_downloaded", doc.id);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  if (!doc)
    return (
      <div className="flex h-full items-center justify-center text-zinc-500">
        {error ?? "Loading…"}
      </div>
    );

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center gap-3 border-b border-zinc-800 px-4 py-2">
        <button
          onClick={onBack}
          className="rounded px-2 py-1 text-sm text-zinc-400 hover:bg-zinc-900 hover:text-zinc-200"
        >
          ← Documents
        </button>
        <span className="truncate text-sm font-medium">{doc.filename}</span>
        {locked && (
          <span className="rounded bg-emerald-900/60 px-2 py-0.5 text-xs font-medium text-emerald-300">
            APPROVED · locked
          </span>
        )}

        {doc.pages.length > 1 && (
          <div className="flex gap-1">
            {doc.pages.map((p, i) => (
              <button
                key={p.id}
                onClick={() => setPageIdx(i)}
                className={`rounded px-2 py-0.5 text-xs ${
                  i === pageIdx
                    ? "bg-zinc-700 text-zinc-100"
                    : "bg-zinc-900 text-zinc-500 hover:text-zinc-300"
                }`}
              >
                p{i + 1}
              </button>
            ))}
          </div>
        )}

        <div className="ml-auto flex items-center gap-2 text-xs">
          {!locked && (
            <>
              <label className="flex items-center gap-1.5 text-zinc-400">
                <input
                  type="checkbox"
                  checked={vlm}
                  onChange={(e) => setVlm(e.target.checked)}
                />
                VLM
              </label>
              <button
                onClick={() => runJob(vlm)}
                disabled={jobRunning}
                className="rounded bg-emerald-700 px-3 py-1.5 font-medium hover:bg-emerald-600 disabled:opacity-50"
              >
                {jobRunning ? "Running…" : "Run extraction"}
              </button>
              <div className="flex items-center gap-1">
                <select
                  value={addKind}
                  onChange={(e) => setAddKind(e.target.value as CutoutKind)}
                  className="rounded border border-zinc-700 bg-zinc-900 px-1.5 py-1"
                >
                  {KINDS.map((k) => (
                    <option key={k}>{k}</option>
                  ))}
                </select>
                <button
                  onClick={() => setDrawMode(drawMode === "add" ? null : "add")}
                  className={`rounded px-3 py-1.5 ${
                    drawMode === "add"
                      ? "bg-cyan-600"
                      : "bg-zinc-800 hover:bg-zinc-700"
                  }`}
                >
                  {drawMode === "add" ? "Draw…" : "+ Add"}
                </button>
                <button
                  onClick={() =>
                    setDrawMode(drawMode === "add-poly" ? null : "add-poly")
                  }
                  className={`rounded px-3 py-1.5 ${
                    drawMode === "add-poly"
                      ? "bg-cyan-600"
                      : "bg-zinc-800 hover:bg-zinc-700"
                  }`}
                >
                  ✎ Freeform
                </button>
              </div>
            </>
          )}
          <button
            onClick={exportDoc}
            className="rounded bg-zinc-800 px-3 py-1.5 hover:bg-zinc-700"
          >
            Export
          </button>
          <button
            onClick={() => setShowSummary(true)}
            className="rounded bg-zinc-800 px-3 py-1.5 hover:bg-zinc-700"
          >
            Summary
          </button>
        </div>
      </header>

      <JobProgress events={events} pageCount={doc.page_count} />
      {scale.pages.length > 0 && (
        <ScaleBanner
          scale={scale}
          locked={locked}
          busy={busy}
          onSetScale={async (pageId, value) => {
            setBusy(true);
            setError(null);
            try {
              await api.setPageScale(pageId, value, sessionId);
              refetch();
            } catch (e) {
              setError((e as Error).message);
            } finally {
              setBusy(false);
            }
          }}
        />
      )}
      {error && (
        <div className="border-b border-red-900 bg-red-950/60 px-4 py-1.5 text-xs text-red-300">
          {error}
        </div>
      )}

      <div className="flex min-h-0 flex-1">
        <div className="min-w-0 flex-1">
          {page && (
            <PageViewer
              page={page}
              cutouts={filtered}
              selectedId={selectedId}
              onSelect={setSelectedId}
              drawMode={locked ? null : drawMode}
              onRect={onRect}
              onPolygon={onPolygon}
              finalizeThreshold={thresholds.finalize}
              highlightIds={highlightIds}
            />
          )}
        </div>
        <aside className="flex w-80 flex-col border-l border-zinc-800">
          <div className="flex border-b border-zinc-800 text-xs">
            {(["bom", "list"] as const).map((tab) => (
              <button
                key={tab}
                onClick={() => {
                  setSidebarTab(tab);
                  setHighlightIds(null);
                }}
                className={`flex-1 px-3 py-2 font-medium uppercase tracking-wide ${
                  sidebarTab === tab
                    ? "bg-zinc-800 text-zinc-100"
                    : "text-zinc-500 hover:text-zinc-300"
                }`}
              >
                {tab === "bom" ? "BOM" : "List"}
              </button>
            ))}
          </div>
          {sidebarTab === "bom" ? (
            <BomPanel
              docId={docId}
              rows={bomRows}
              cutouts={cutouts}
              finalizeThreshold={thresholds.finalize}
              locked={locked}
              busy={busy}
              onHighlight={setHighlightIds}
              onReject={(id) =>
                mutate(() =>
                  api.patchCutout(id, { action: "reject", session_id: sessionId }),
                )
              }
              onRestore={(id) =>
                mutate(() =>
                  api.patchCutout(id, { action: "approve", session_id: sessionId }),
                )
              }
              onRejectGroup={rejectGroup}
              onFinalize={finalize}
            />
          ) : (
            <>
              <CutoutSidebar
                cutouts={pageCutouts}
                filtered={filtered}
                filters={filters}
                onFilters={setFilters}
                selectedId={selectedId}
                onSelect={setSelectedId}
                escalationThreshold={thresholds.escalation}
              />
              {selected && (
                <CutoutDetail
                  key={selected.id}
                  cutout={selected}
                  busy={busy || locked}
                  onAction={(action) =>
                    mutate(() =>
                      api.patchCutout(selected.id, {
                        action,
                        session_id: sessionId,
                      }),
                    )
                  }
                  onKind={(kind) =>
                    mutate(() =>
                      api.patchCutout(selected.id, {
                        action: "edit",
                        kind,
                        session_id: sessionId,
                      }),
                    )
                  }
                  onRedraw={() =>
                    setDrawMode(drawMode === "edit" ? null : "edit")
                  }
                  redrawing={drawMode === "edit"}
                />
              )}
            </>
          )}
        </aside>
      </div>

      {pendingPolygon && (
        <KindModal
          onPick={addPolygonCutout}
          onCancel={() => {
            setPendingPolygon(null);
            setDrawMode(null);
          }}
        />
      )}
      {showSummary && (
        <SummaryPanel docId={docId} onClose={() => setShowSummary(false)} />
      )}
    </div>
  );
}
