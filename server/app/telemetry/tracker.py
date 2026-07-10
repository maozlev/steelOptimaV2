import json

from sqlalchemy.orm import Session

from app.db.models import TelemetryEvent


def emit(
    db: Session,
    type_: str,
    entity_id: int | None = None,
    payload: dict | None = None,
    session_id: str | None = None,
) -> None:
    """Queue a telemetry event on the caller's session; the caller commits."""
    db.add(
        TelemetryEvent(
            type=type_,
            entity_id=entity_id,
            payload_json=json.dumps(payload) if payload is not None else None,
            session_id=session_id,
        )
    )
