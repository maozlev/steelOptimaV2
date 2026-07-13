export interface DocumentOut {
  id: number;
  filename: string;
  page_count: number;
  status: "pending" | "approved";
  created_at: string;
}

export interface ConfigOut {
  escalation_threshold: number;
  finalize_threshold: number;
}

export interface FinalizeOut {
  document: DocumentOut;
  auto_approved: number;
  auto_rejected: number;
  already_reviewed: number;
}

export interface CropIn {
  x_min: number;
  y_min: number;
  x_max: number;
  y_max: number;
}

export interface PageOut {
  id: number;
  index: number;
  kind: "vector" | "raster" | "mixed";
  width_pt: number;
  height_pt: number;
  render_dpi: number;
}

export interface DocumentDetailOut extends DocumentOut {
  pages: PageOut[];
}

export interface JobOut {
  id: number;
  document_id: number;
  status: "queued" | "running" | "done" | "failed";
  started_at: string;
  finished_at: string | null;
  error: string | null;
  cutout_count: number;
}

export type CutoutStatus = "pending" | "approved" | "rejected" | "edited";
export type CutoutKind = "hole" | "slot" | "notch" | "freeform";
export type CutoutSource = "vector" | "raster_cv" | "vlm" | "fused" | "manual";

export interface CutoutOut {
  id: number;
  page_id: number;
  job_id: number | null;
  kind: CutoutKind;
  source: CutoutSource;
  confidence: number;
  status: CutoutStatus;
  bbox: [number, number, number, number];
  geometry_wkt: string;
  dimension_text: string | null;
  measured_dims_json: string | null;
  edited_geometry_wkt: string | null;
}

export interface HealthOut {
  status: string;
  ollama: { available: boolean; models: string[] };
}

export interface JobEvent {
  type: string;
  [key: string]: unknown;
}

export interface SummaryBucket {
  bucket?: string;
  pending: number;
  approved: number;
  rejected: number;
  edited: number;
  reviewed: number;
  total: number;
  approve_rate: number | null;
}

export interface TelemetrySummary {
  document_id: number | null;
  escalation_threshold: number;
  by_source: Record<string, SummaryBucket>;
  by_confidence: SummaryBucket[];
  vlm: { calls: number; ok_rate: number | null; avg_latency_ms: number | null };
}

// Shape is derived server-side from geometry, not read off CutoutKind: the DB
// stores a true rectangle and an obround slot under the same "slot" kind.
export type BomShape = "circle" | "rectangle" | "slot" | "notch" | "irregular";

export interface BomRow {
  key: string;
  shape: BomShape;
  shape_label: string;
  dims: string;
  qty: number;
  cut_length_each_mm: number;
  cut_length_total_mm: number;
  pending_qty: number;
  cutout_ids: number[];
  rejected_ids: number[];
  documents?: string[]; // aggregate only
}

export interface BomTotals {
  qty: number;
  cut_length_mm: number;
  pending_qty: number;
}

export interface DocumentBom {
  document: { id: number; filename: string; status: string };
  rows: BomRow[];
  totals: BomTotals;
}

export interface AggregateBom {
  documents: { id: number; filename: string }[];
  rows: BomRow[];
  totals: BomTotals;
}
