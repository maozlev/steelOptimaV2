from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

PageKind = Enum("vector", "raster", "mixed", name="page_kind")
JobStatus = Enum("queued", "running", "done", "failed", name="job_status")
CutoutKind = Enum("hole", "slot", "notch", "freeform", name="cutout_kind")
CutoutSource = Enum("vector", "raster_cv", "vlm", "fused", "manual", name="cutout_source")
CutoutStatus = Enum("pending", "approved", "rejected", "edited", name="cutout_status")
DocumentStatus = Enum("pending", "approved", name="document_status")
TableKind = Enum("materials", "coordinates", "other", "unknown", name="table_kind")
TableStatus = Enum("pending", "approved", "rejected", name="table_status")
RowStatus = Enum(
    "auto_approved", "needs_review", "approved", "rejected", "edited", name="row_status"
)
PricingUnit = Enum("per_kg", "per_m", "per_unit", name="pricing_unit")
ChatScope = Enum("document", "project", "summary", name="chat_scope")
ChatRole = Enum("user", "assistant", name="chat_role")


class Base(DeclarativeBase):
    pass


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # what this project's scans look for — the USER decides at creation:
    # "tables" (material tables → summary/bid/orders) or "cutouts" (holes &
    # shapes). A table scanner pointed at a shape drawing hallucinates a BOM
    # out of the title block; the project kind is what prevents that.
    # add_missing_columns backfills "" on old rows — read it as "tables".
    kind: Mapped[str] = mapped_column(String(16), default="tables")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    documents: Mapped[list["Document"]] = relationship(
        back_populates="project", order_by="Document.created_at"
    )


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    filename: Mapped[str] = mapped_column(String(255))
    sha256: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    path: Mapped[str] = mapped_column(String(1024))
    page_count: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(DocumentStatus, default="pending")
    crop_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id"), index=True, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    pages: Mapped[list["Page"]] = relationship(
        back_populates="document", cascade="all, delete-orphan", order_by="Page.index"
    )
    project: Mapped[Project | None] = relationship(back_populates="documents")


class Page(Base):
    __tablename__ = "pages"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), index=True)
    index: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(PageKind)
    width_pt: Mapped[float]
    height_pt: Mapped[float]
    render_path: Mapped[str] = mapped_column(String(1024))
    render_dpi: Mapped[int] = mapped_column(Integer)

    # real_mm / paper_mm. A 1:5 sheet is 5.0; a 2:1 magnified sheet is 0.5.
    # THE OPERATOR OWNS THIS NUMBER. The detector proposes; a human confirms. A document
    # cannot be finalized until scale_confirmed is true — nothing is ever cut from a size
    # nobody signed off on.
    scale: Mapped[float | None] = mapped_column(Float, nullable=True)
    # a human has explicitly accepted this scale
    scale_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    # what the drawing's own dimensions say, kept even after the operator overrides — it
    # is the only thing that can catch a typo. 1:50 for a 1:5 sheet cuts every part ten
    # times too big, and does it silently.
    scale_detected: Mapped[float | None] = mapped_column(Float, nullable=True)
    # True when the detector's own cross-check backed its estimate up
    scale_confident: Mapped[bool] = mapped_column(Boolean, default=False)
    scale_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    document: Mapped[Document] = relationship(back_populates="pages")


class ExtractionJob(Base):
    __tablename__ = "extraction_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), index=True)
    status: Mapped[str] = mapped_column(JobStatus, default="queued")
    # dispatch discriminator: "tables" runs the material-table pipeline; anything
    # else (incl. the "" that add_missing_columns backfills) runs cutout extraction
    kind: Mapped[str] = mapped_column(String(32), default="cutouts")
    params_json: Mapped[str] = mapped_column(Text, default="{}")
    started_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class VlmCall(Base):
    __tablename__ = "vlm_calls"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("extraction_jobs.id"), index=True)
    cutout_id: Mapped[int | None] = mapped_column(
        ForeignKey("cutouts.id"), nullable=True
    )
    table_id: Mapped[int | None] = mapped_column(
        ForeignKey("material_tables.id"), nullable=True
    )
    trigger: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(128))
    prompt_hash: Mapped[str] = mapped_column(String(64))
    crop_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer)
    response_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    ok: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Cutout(Base):
    __tablename__ = "cutouts"

    id: Mapped[int] = mapped_column(primary_key=True)
    page_id: Mapped[int] = mapped_column(ForeignKey("pages.id"), index=True)
    job_id: Mapped[int | None] = mapped_column(
        ForeignKey("extraction_jobs.id"), index=True, nullable=True
    )  # null for manual cutouts
    geometry_wkt: Mapped[str] = mapped_column(Text)
    bbox: Mapped[str] = mapped_column(String(128))  # JSON [x0, y0, x1, y1] in page pt
    kind: Mapped[str] = mapped_column(CutoutKind)
    source: Mapped[str] = mapped_column(CutoutSource)
    confidence: Mapped[float] = mapped_column(Float)
    dimension_text: Mapped[str | None] = mapped_column(String(255), nullable=True)
    measured_dims_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(CutoutStatus, default="pending")
    edited_geometry_wkt: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class MaterialTable(Base):
    __tablename__ = "material_tables"

    id: Mapped[int] = mapped_column(primary_key=True)
    page_id: Mapped[int] = mapped_column(ForeignKey("pages.id"), index=True)
    job_id: Mapped[int | None] = mapped_column(
        ForeignKey("extraction_jobs.id"), index=True, nullable=True
    )
    bbox: Mapped[str] = mapped_column(String(128))  # JSON [x0, y0, x1, y1] rotated pt
    n_rows: Mapped[int] = mapped_column(Integer)  # data rows, header excluded
    n_cols: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(TableKind, default="unknown")
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    # per-column role map: ["item_no", "qty", "description", ...]
    columns_json: Mapped[str] = mapped_column(Text, default="[]")
    header_rows: Mapped[int] = mapped_column(Integer, default=1)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    # a "Total Weight: 3814.4 kg" style footer, when the sheet declares one — it is
    # a checksum over the whole weight column and drives table-level validation
    declared_total_weight_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    validation_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(TableStatus, default="pending")
    crop_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    rows: Mapped[list["MaterialRow"]] = relationship(
        back_populates="table", cascade="all, delete-orphan", order_by="MaterialRow.row_index"
    )


class MaterialRow(Base):
    __tablename__ = "material_rows"

    id: Mapped[int] = mapped_column(primary_key=True)
    table_id: Mapped[int] = mapped_column(ForeignKey("material_tables.id"), index=True)
    row_index: Mapped[int] = mapped_column(Integer)
    # per-cell provenance: [{"col", "raw_ocr", "ocr_conf", "vlm_value", "value", "source"}]
    cells_json: Mapped[str] = mapped_column(Text, default="[]")
    material_key: Mapped[str | None] = mapped_column(String(255), index=True, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    qty: Mapped[float | None] = mapped_column(Float, nullable=True)
    unit_length_mm: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_length_mm: Mapped[float | None] = mapped_column(Float, nullable=True)
    unit_weight_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_weight_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    # plates: a mixed BOM reuses the length columns for W×H ("890x185") and total
    # area ("0.6495 m²"), which parse_number rightly refuses — these fields hold
    # the plate reading so the data isn't stranded in cells_json (and area×THK×
    # density validates against the weight column; see validate.py)
    width_mm: Mapped[float | None] = mapped_column(Float, nullable=True)
    height_mm: Mapped[float | None] = mapped_column(Float, nullable=True)
    area_m2: Mapped[float | None] = mapped_column(Float, nullable=True)
    thk_mm: Mapped[float | None] = mapped_column(Float, nullable=True)
    flags_json: Mapped[str] = mapped_column(Text, default="[]")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(RowStatus, default="needs_review")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    table: Mapped[MaterialTable] = relationship(back_populates="rows")


class MaterialPrice(Base):
    __tablename__ = "material_prices"

    id: Mapped[int] = mapped_column(primary_key=True)
    # NULL project_id = the user's global price book, used as fallback
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id"), index=True, nullable=True
    )
    material_key: Mapped[str] = mapped_column(String(255), index=True)
    price: Mapped[float] = mapped_column(Float)
    pricing_unit: Mapped[str] = mapped_column(PricingUnit)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class OrderPlan(Base):
    __tablename__ = "order_plans"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    params_json: Mapped[str] = mapped_column(Text)  # kerf, stock catalog, pieces
    result_json: Mapped[str] = mapped_column(Text)  # bars, order lines, waste, cost
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ChatMessage(Base):
    """One turn of a scoped Q&A chat.

    scope/scope_id pin the conversation to what it may talk about: a single
    document, a single project, or the cross-project summary (scope_id 0).
    The context itself is rebuilt fresh from the DB on every question — only
    the conversation is stored, never a stale snapshot of the data.
    """

    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    scope: Mapped[str] = mapped_column(ChatScope, index=True)
    scope_id: Mapped[int] = mapped_column(Integer, index=True)
    role: Mapped[str] = mapped_column(ChatRole)
    content: Mapped[str] = mapped_column(Text)
    # what the model was told when it answered; kept for debugging bad answers
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    context_chars: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class TelemetryEvent(Base):
    __tablename__ = "telemetry_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    type: Mapped[str] = mapped_column(String(64), index=True)
    entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)
