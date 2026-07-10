import json
from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator


class JobCreateIn(BaseModel):
    vlm: bool | None = None


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    document_id: int
    status: str
    started_at: datetime
    finished_at: datetime | None
    error: str | None
    cutout_count: int = 0


class CutoutOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    page_id: int
    job_id: int | None
    kind: str
    source: str
    confidence: float
    status: str
    bbox: list[float]
    geometry_wkt: str
    dimension_text: str | None
    measured_dims_json: str | None
    edited_geometry_wkt: str | None = None

    @field_validator("bbox", mode="before")
    @classmethod
    def _parse_bbox(cls, v):
        return json.loads(v) if isinstance(v, str) else v
