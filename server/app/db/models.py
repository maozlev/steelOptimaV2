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


class Base(DeclarativeBase):
    pass


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    filename: Mapped[str] = mapped_column(String(255))
    sha256: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    path: Mapped[str] = mapped_column(String(1024))
    page_count: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(DocumentStatus, default="pending")
    crop_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    pages: Mapped[list["Page"]] = relationship(
        back_populates="document", cascade="all, delete-orphan", order_by="Page.index"
    )


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

    document: Mapped[Document] = relationship(back_populates="pages")


class ExtractionJob(Base):
    __tablename__ = "extraction_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), index=True)
    status: Mapped[str] = mapped_column(JobStatus, default="queued")
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


class TelemetryEvent(Base):
    __tablename__ = "telemetry_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    type: Mapped[str] = mapped_column(String(64), index=True)
    entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)
