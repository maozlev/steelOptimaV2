from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator

from app.schemas.documents import DocumentOut

PROJECT_KINDS = ("tables", "cutouts")


class ProjectIn(BaseModel):
    name: str
    note: str | None = None
    kind: str = "tables"


class ProjectPatchIn(BaseModel):
    name: str | None = None
    note: str | None = None
    kind: str | None = None


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    note: str | None
    kind: str = "tables"
    created_at: datetime

    @field_validator("kind", mode="before")
    @classmethod
    def _legacy_kind(cls, v):
        # rows that predate the column were backfilled with "" — they are tables
        return v if v in PROJECT_KINDS else "tables"


class ProjectListOut(ProjectOut):
    document_count: int
    table_count: int
    needs_review_rows: int


class ProjectDocumentOut(DocumentOut):
    table_count: int = 0
    needs_review_rows: int = 0
    cutout_count: int = 0
    pending_cutouts: int = 0
    # latest scan job of the PROJECT's kind (name kept for client compatibility)
    last_table_job_status: str | None = None


class ProjectDetailOut(ProjectOut):
    documents: list[ProjectDocumentOut]
