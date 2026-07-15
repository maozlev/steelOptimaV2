from datetime import datetime

from pydantic import BaseModel, ConfigDict


class PageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    index: int
    kind: str
    width_pt: float
    height_pt: float
    render_dpi: int


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    filename: str
    sha256: str
    page_count: int
    status: str
    project_id: int | None
    created_at: datetime


class DocumentDetailOut(DocumentOut):
    pages: list[PageOut]


class DocumentCropIn(BaseModel):
    x_min: float
    y_min: float
    x_max: float
    y_max: float


class FinalizeIn(BaseModel):
    session_id: str | None = None


class FinalizeOut(BaseModel):
    document: DocumentOut
    auto_approved: int
    auto_rejected: int
    already_reviewed: int
