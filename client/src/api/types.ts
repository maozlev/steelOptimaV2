export interface DocumentOut {
  id: number;
  filename: string;
  page_count: number;
  status: "pending" | "approved";
  project_id: number | null;
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
  /** Nothing in this row clears the finalize threshold — finalize will auto-reject every
   *  member. Still shown (a missed hole costs a part) but it is not part of the work
   *  order, and must not be listed among the things to cut. */
  needs_review: boolean;
  cutout_ids: number[];
  rejected_ids: number[];
  documents?: string[]; // aggregate only
}

export interface BomTotals {
  qty: number;
  cut_length_mm: number;
  pending_qty: number;
}

/** Dimensions are measured in PAPER mm and multiplied by the sheet scale. Without a
 *  verified scale the numbers are the size of ink on a page, not of a part. */
export interface PageScale {
  page_index: number;
  page_id: number;
  /** real_mm / paper_mm. A 1:5 sheet is 5.0; a 2:1 magnified sheet is 0.5.
   *  THE OPERATOR OWNS THIS. Finalize is blocked until it is confirmed. */
  scale: number | null;
  /** what the drawing's own dimensions say — kept even after an override, because it is
   *  the only thing that can catch a typo */
  detected: number | null;
  confirmed: boolean;
  confident: boolean;
  /** set when the operator's scale contradicts the drawing — a mistyped 1:50 on a 1:5
   *  sheet cuts every part ten times too big, and does it silently */
  disagreement: string | null;
  note: string | null;
}

export interface ScaleStatus {
  pages: PageScale[];
  /** every page's scale has been confirmed by a human */
  trustworthy: boolean;
}

export interface DocumentBom {
  document: { id: number; filename: string; status: string };
  scale: ScaleStatus;
  rows: BomRow[];
  totals: BomTotals;
}

/** What this project's scans look for — the user decides at creation. */
export type ProjectKind = "tables" | "cutouts";

export interface ProjectOut {
  id: number;
  name: string;
  note: string | null;
  kind: ProjectKind;
  created_at: string;
}

export interface ProjectListOut extends ProjectOut {
  document_count: number;
  table_count: number;
  needs_review_rows: number;
}

export interface ProjectDocumentOut extends DocumentOut {
  table_count: number;
  needs_review_rows: number;
  cutout_count: number;
  pending_cutouts: number;
  /** latest scan job of the PROJECT's kind (tables or cutouts) */
  last_table_job_status: "queued" | "running" | "done" | "failed" | null;
}

export interface ProjectDetailOut extends ProjectOut {
  documents: ProjectDocumentOut[];
}

export type TableKind = "materials" | "coordinates" | "other" | "unknown";
export type TableStatus = "pending" | "approved" | "rejected";
export type RowStatus =
  | "auto_approved"
  | "needs_review"
  | "approved"
  | "rejected"
  | "edited";

export interface MaterialCell {
  col: number;
  raw_ocr: string | null;
  ocr_conf: number;
  vlm_value: string | null;
  value: string | null;
  source: "ocr" | "vlm" | "fused" | "empty" | "manual";
}

export interface MaterialRowOut {
  id: number;
  table_id: number;
  row_index: number;
  cells: MaterialCell[];
  material_key: string | null;
  description: string | null;
  qty: number | null;
  unit_length_mm: number | null;
  total_length_mm: number | null;
  unit_weight_kg: number | null;
  total_weight_kg: number | null;
  flags: string[];
  confidence: number;
  status: RowStatus;
}

export interface MaterialTableOut {
  id: number;
  page_id: number;
  job_id: number | null;
  bbox: [number, number, number, number];
  n_rows: number;
  n_cols: number;
  kind: TableKind;
  title: string | null;
  columns: { index: number; role: string }[];
  header_rows: number;
  confidence: number;
  declared_total_weight_kg: number | null;
  validation: {
    declared_total_weight_kg: number | null;
    summed_total_weight_kg: number | null;
    weight_total_matches: boolean | null;
  } | null;
  status: TableStatus;
  row_count: number;
  needs_review_rows: number;
  auto_approved_rows: number;
}

export interface MaterialTableDetailOut extends MaterialTableOut {
  rows: MaterialRowOut[];
}

export interface SummaryRow {
  material_key: string;
  description: string | null;
  qty: number;
  total_length_mm: number;
  total_weight_kg: number;
  lengths: { unit_length_mm: number; qty: number }[];
  documents: string[];
  projects: string[];
  row_ids: number[];
}

export interface ProjectSummary {
  projects: { id: number; name: string }[];
  rows: SummaryRow[];
  totals: { qty: number; total_weight_kg: number; total_length_mm: number };
  unreviewed: { pending_tables: number; needs_review_rows: number };
}

export type PricingUnit = "per_kg" | "per_m" | "per_unit";

export interface PriceEntry {
  material_key: string;
  price: number;
  pricing_unit: PricingUnit;
}

export interface BidRow extends SummaryRow {
  price: number | null;
  pricing_unit: PricingUnit | null;
  line_total: number | null;
}

export interface BidOut {
  projects: { id: number; name: string }[];
  rows: BidRow[];
  total: number;
  unpriced_keys: string[];
  unreviewed: { pending_tables: number; needs_review_rows: number };
}

export interface OrderLine {
  stock_length_mm: number;
  count: number;
  unit_price: number;
  subtotal: number;
}

export interface OrderPlanResult {
  order: OrderLine[];
  total_cost: number;
  bars: { stock_length_mm: number; price: number; cuts: number[]; waste_mm: number }[];
  kerf_mm: number;
  total_bought_mm: number;
  total_used_mm: number;
  waste_pct: number;
  infeasible_lengths_mm: number[];
}

export interface OrderPlanOut {
  id: number;
  project_id: number;
  created_at: string;
  params: {
    material_key: string | null;
    pieces: { length_mm: number; qty: number }[];
    stock: { length_mm: number; price: number }[];
    kerf_mm: number;
  };
  result: OrderPlanResult;
}

export interface AggregateBom {
  documents: { id: number; filename: string }[];
  untrusted_scale: string[];
  rows: BomRow[];
  totals: BomTotals;
}

// --- scoped Q&A chat -------------------------------------------------------

export type ChatScope = "document" | "project" | "summary";

export interface ChatMessageOut {
  id: number;
  role: "user" | "assistant";
  content: string;
  created_at: string;
}

// --- per-project scan queue -------------------------------------------------

export interface QueueEntry {
  job_id: number;
  document_id: number;
  filename: string;
  status: "queued" | "running" | "failed";
  queue_position: number | null;
  started_at: string | null;
  error: string | null;
}

export interface ProjectQueueOut {
  total_documents: number;
  scanned: number;
  running: QueueEntry[];
  queued: QueueEntry[];
  failed: QueueEntry[];
  unscanned: { document_id: number; filename: string }[];
  avg_scan_seconds: number | null;
  eta_seconds: number | null;
}
