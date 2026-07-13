import json
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _parse_json(v):
    return json.loads(v) if isinstance(v, str) else v


class MaterialRowOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: int
    table_id: int
    row_index: int
    cells: list = Field(default=[], validation_alias="cells_json")
    material_key: str | None
    description: str | None
    qty: float | None
    unit_length_mm: float | None
    total_length_mm: float | None
    unit_weight_kg: float | None
    total_weight_kg: float | None
    flags: list = Field(default=[], validation_alias="flags_json")
    confidence: float
    status: str
    updated_at: datetime

    @field_validator("cells", "flags", mode="before")
    @classmethod
    def _json_fields(cls, v):
        return _parse_json(v)


class MaterialTableOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: int
    page_id: int
    job_id: int | None
    bbox: list[float]
    n_rows: int
    n_cols: int
    kind: str
    title: str | None
    columns: list = Field(default=[], validation_alias="columns_json")
    header_rows: int
    confidence: float
    declared_total_weight_kg: float | None
    validation: dict | None = Field(default=None, validation_alias="validation_json")
    status: str
    row_count: int = 0
    needs_review_rows: int = 0
    auto_approved_rows: int = 0

    @field_validator("bbox", mode="before")
    @classmethod
    def _bbox(cls, v):
        return _parse_json(v)

    @field_validator("columns", mode="before")
    @classmethod
    def _columns(cls, v):
        return _parse_json(v)

    @field_validator("validation", mode="before")
    @classmethod
    def _validation(cls, v):
        return _parse_json(v)


class MaterialTableDetailOut(MaterialTableOut):
    rows: list[MaterialRowOut] = []
