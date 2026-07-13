import type {
  AggregateBom,
  ConfigOut,
  CropIn,
  CutoutKind,
  CutoutOut,
  DocumentBom,
  DocumentDetailOut,
  DocumentOut,
  FinalizeOut,
  HealthOut,
  JobOut,
  TelemetrySummary,
} from "./types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, init);
  if (!r.ok) {
    let detail = r.statusText;
    try {
      detail = (await r.json()).detail ?? detail;
    } catch {
      /* not json */
    }
    throw new Error(detail);
  }
  // 204 No Content has no body
  if (r.status === 204) return undefined as T;
  return r.json();
}

const json = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const api = {
  health: () => request<HealthOut>("/api/health"),
  getConfig: () => request<ConfigOut>("/api/config"),

  listDocuments: () => request<DocumentOut[]>("/api/documents"),
  getDocument: (id: number) => request<DocumentDetailOut>(`/api/documents/${id}`),
  uploadDocument(file: File) {
    const form = new FormData();
    form.append("file", file);
    return request<DocumentDetailOut>("/api/documents", {
      method: "POST",
      body: form,
    });
  },
  deleteDocument: (docId: number) =>
    request<void>(`/api/documents/${docId}`, { method: "DELETE" }),
  cropDocument: (docId: number, crop: CropIn) =>
    request<DocumentDetailOut>(`/api/documents/${docId}/crop`, json("POST", crop)),
  finalizeDocument: (docId: number, sessionId?: string) =>
    request<FinalizeOut>(
      `/api/documents/${docId}/finalize`,
      json("POST", { session_id: sessionId }),
    ),

  startJob: (docId: number, vlm: boolean) =>
    request<JobOut>(`/api/documents/${docId}/jobs`, json("POST", { vlm })),
  getJob: (id: number) => request<JobOut>(`/api/jobs/${id}`),

  listCutouts: (pageId: number) =>
    request<CutoutOut[]>(`/api/pages/${pageId}/cutouts`),
  patchCutout: (
    id: number,
    body: {
      action: "approve" | "reject" | "edit";
      geometry_wkt?: string;
      kind?: CutoutKind;
      session_id?: string;
    },
  ) => request<CutoutOut>(`/api/cutouts/${id}`, json("PATCH", body)),
  addCutout: (
    pageId: number,
    body: { geometry_wkt: string; kind: CutoutKind; session_id?: string },
  ) => request<CutoutOut>(`/api/pages/${pageId}/cutouts`, json("POST", body)),

  exportDocument: (docId: number) =>
    request<Record<string, unknown>>(`/api/documents/${docId}/export`),
  listDocumentCutouts: (docId: number) =>
    request<CutoutOut[]>(`/api/documents/${docId}/cutouts`),

  // BOM rows (shape, size, qty, cut length) are computed server-side so the
  // workspace, the aggregate view and the export can never disagree.
  getDocumentBom: (docId: number) =>
    request<DocumentBom>(`/api/documents/${docId}/bom`),
  getAggregateBom: () => request<AggregateBom>("/api/bom/aggregate"),

  telemetrySummary: (docId?: number) =>
    request<TelemetrySummary>(
      docId == null
        ? "/api/telemetry/summary"
        : `/api/telemetry/summary?document_id=${docId}`,
    ),
  postTelemetry: (batch: {
    session_id: string;
    events: { type: string; entity_id?: number; payload?: object }[];
  }) => request<{ accepted: number }>("/api/telemetry/events", json("POST", batch)),
};

export const renderUrl = (pageId: number, overlay = false, v?: string) => {
  const params = new URLSearchParams();
  if (overlay) params.set("overlay", "true");
  if (v) params.set("v", v);
  return `/api/pages/${pageId}/render?${params}`;
};
