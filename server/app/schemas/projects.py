from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.schemas.documents import DocumentOut


class ProjectIn(BaseModel):
    name: str
    note: str | None = None


class ProjectPatchIn(BaseModel):
    name: str | None = None
    note: str | None = None


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    note: str | None
    created_at: datetime


class ProjectListOut(ProjectOut):
    document_count: int
    table_count: int
    needs_review_rows: int


class ProjectDocumentOut(DocumentOut):
    table_count: int = 0
    needs_review_rows: int = 0
    last_table_job_status: str | None = None


class ProjectDetailOut(ProjectOut):
    documents: list[ProjectDocumentOut]
