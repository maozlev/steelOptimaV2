import type {
  AggregateBom,
  AnalyzeResult,
  ChatMessageOut,
  ChatScope,
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
  BidOut,
  MaterialRowOut,
  MaterialTableDetailOut,
  MaterialTableOut,
  OrderPlanOut,
  PageScale,
  PriceEntry,
  ProjectDetailOut,
  ProjectListOut,
  ProjectOut,
  ProjectQueueOut,
  ProjectSummary,
  TableKind,
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
  moveDocument: (docId: number, projectId: number) =>
    request<DocumentOut>(
      `/api/documents/${docId}`,
      json("PATCH", { project_id: projectId }),
    ),
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
  setPageScale: (pageId: number, scale: number, sessionId?: string) =>
    request<PageScale>(
      `/api/pages/${pageId}/scale`,
      json("PATCH", { scale, session_id: sessionId }),
    ),

  listProjects: () => request<ProjectListOut[]>("/api/projects"),
  createProject: (name: string, kind: "tables" | "cutouts" = "tables", note?: string) =>
    request<ProjectOut>("/api/projects", json("POST", { name, note, kind })),
  getProject: (id: number) => request<ProjectDetailOut>(`/api/projects/${id}`),
  patchProject: (
    id: number,
    body: { name?: string; note?: string; kind?: "tables" | "cutouts" },
  ) => request<ProjectOut>(`/api/projects/${id}`, json("PATCH", body)),
  deleteProject: (id: number) =>
    request<void>(`/api/projects/${id}`, { method: "DELETE" }),
  uploadProjectDocument(projectId: number, file: File) {
    const form = new FormData();
    form.append("file", file);
    return request<DocumentDetailOut>(`/api/projects/${projectId}/documents`, {
      method: "POST",
      body: form,
    });
  },

  /** Analyze a drawing (PDF/PNG/JPEG or whiteboard sketch) into proposed plan items. */
  analyzeDrawing(file: File | Blob, filename = "sketch.png") {
    const form = new FormData();
    form.append("file", file, file instanceof File ? file.name : filename);
    return request<AnalyzeResult>(`/api/planning/analyze`, {
      method: "POST",
      body: form,
    });
  },

  startTableJob: (docId: number) =>
    request<JobOut>(`/api/documents/${docId}/table-jobs`, json("POST", {})),
  startProjectTableJobs: (projectId: number, onlyFailed = false) =>
    request<JobOut[]>(
      `/api/projects/${projectId}/table-jobs${onlyFailed ? "?only_failed=true" : ""}`,
      { method: "POST" },
    ),
  getProjectQueue: (projectId: number) =>
    request<ProjectQueueOut>(`/api/projects/${projectId}/queue`),
  cancelJob: (jobId: number) =>
    request<JobOut>(`/api/jobs/${jobId}`, { method: "DELETE" }),
  listDocumentTables: (docId: number) =>
    request<MaterialTableOut[]>(`/api/documents/${docId}/tables`),
  getTable: (tableId: number) =>
    request<MaterialTableDetailOut>(`/api/tables/${tableId}`),
  patchTable: (
    tableId: number,
    body: { action: "approve" | "reject" | "reopen" | "set_kind"; kind?: TableKind },
  ) => request<MaterialTableOut>(`/api/tables/${tableId}`, json("PATCH", body)),
  patchMaterialRow: (
    rowId: number,
    body: {
      action: "approve" | "reject" | "edit";
      fields?: {
        description?: string;
        qty?: number;
        unit_length_mm?: number;
        total_length_mm?: number;
        unit_weight_kg?: number;
        total_weight_kg?: number;
      };
    },
  ) => request<MaterialRowOut>(`/api/material-rows/${rowId}`, json("PATCH", body)),
  getProjectSummary: (projectId: number) =>
    request<ProjectSummary>(`/api/projects/${projectId}/summary`),
  getProjectsSummary: (ids: number[]) =>
    request<ProjectSummary>(`/api/projects-summary?ids=${ids.join(",")}`),

  getPrices: (projectId: number) =>
    request<{ entries: PriceEntry[] }>(`/api/projects/${projectId}/prices`),
  putPrices: (projectId: number, entries: PriceEntry[]) =>
    request<{ written: number }>(
      `/api/projects/${projectId}/prices`,
      json("PUT", { entries }),
    ),
  getBid: (projectId: number, mergeIds?: number[]) =>
    request<BidOut>(
      `/api/projects/${projectId}/bid` +
        (mergeIds?.length ? `?ids=${mergeIds.join(",")}` : ""),
    ),

  createOrderPlan: (
    projectId: number,
    body: {
      stock?: { length_mm: number; price: number }[];
      kerf_mm: number;
      pieces?: { length_mm: number; qty: number }[];
      material_key?: string;
      // 2D sheets plan (plates): both together, stock omitted
      sheets?: { w_mm: number; h_mm: number; price: number }[];
      pieces_2d?: { w_mm: number; h_mm: number; qty: number; key: string }[];
    },
  ) =>
    request<OrderPlanOut>(
      `/api/projects/${projectId}/order-plans`,
      json("POST", body),
    ),
  listOrderPlans: (projectId: number) =>
    request<OrderPlanOut[]>(`/api/projects/${projectId}/order-plans`),

  getChatMessages: (scope: ChatScope, scopeId: number) =>
    request<ChatMessageOut[]>(`/api/chat/${scope}/${scopeId}/messages`),
  clearChat: (scope: ChatScope, scopeId: number) =>
    request<void>(`/api/chat/${scope}/${scopeId}/messages`, { method: "DELETE" }),

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

/** Ask the scoped chat a question, streaming the answer as it is generated.
 *
 * Not part of `api`: the answer arrives as chunked plain text (the model takes
 * seconds to minutes on local hardware), so this reads the body stream and
 * feeds each delta to `onDelta`. Returns the full answer.
 */
export async function sendChatMessage(
  scope: ChatScope,
  scopeId: number,
  content: string,
  onDelta: (text: string) => void,
  signal?: AbortSignal,
): Promise<string> {
  const r = await fetch(`/api/chat/${scope}/${scopeId}/messages`, {
    ...json("POST", { content }),
    signal,
  });
  if (!r.ok || !r.body) {
    let detail = r.statusText;
    try {
      detail = (await r.json()).detail ?? detail;
    } catch {
      /* not json */
    }
    throw new Error(detail);
  }
  const reader = r.body.getReader();
  const decoder = new TextDecoder();
  let full = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    const text = decoder.decode(value, { stream: true });
    if (text) {
      full += text;
      onDelta(text);
    }
  }
  return full;
}

export const tableCropUrl = (tableId: number) => `/api/tables/${tableId}/crop`;

export const renderUrl = (pageId: number, overlay = false, v?: string) => {
  const params = new URLSearchParams();
  if (overlay) params.set("overlay", "true");
  if (v) params.set("v", v);
  return `/api/pages/${pageId}/render?${params}`;
};
