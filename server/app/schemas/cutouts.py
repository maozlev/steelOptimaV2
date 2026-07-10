from typing import Literal

from pydantic import BaseModel, Field


class CutoutPatchIn(BaseModel):
    action: Literal["approve", "reject", "edit"]
    geometry_wkt: str | None = None
    kind: str | None = Field(default=None, pattern="^(hole|slot|notch|freeform)$")
    session_id: str | None = None


class CutoutCreateIn(BaseModel):
    geometry_wkt: str
    kind: str = Field(pattern="^(hole|slot|notch|freeform)$")
    dimension_text: str | None = None
    session_id: str | None = None
