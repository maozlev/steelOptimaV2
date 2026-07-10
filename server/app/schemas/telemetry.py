from datetime import datetime

from pydantic import BaseModel, Field


class TelemetryEventIn(BaseModel):
    type: str = Field(min_length=1, max_length=64)
    entity_id: int | None = None
    payload: dict | None = None


class TelemetryBatchIn(BaseModel):
    session_id: str | None = None
    events: list[TelemetryEventIn] = Field(min_length=1, max_length=500)


class TelemetryEventOut(BaseModel):
    id: int
    ts: datetime
    session_id: str | None
    type: str
    entity_id: int | None
