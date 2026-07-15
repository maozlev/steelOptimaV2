"""Scoped Q&A chat: one endpoint family, three scopes.

POST streams the answer as chunked plain text — on this hardware the model
takes seconds to minutes, and a reply that trickles in beats a spinner that
looks like a hang. History is persisted server-side so a reopened panel shows
the conversation.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.chat import service
from app.config import settings
from app.db.models import ChatMessage, Document, Project
from app.db.session import get_db
from app.vlm.client import OllamaVlmClient

router = APIRouter(prefix="/api/chat", tags=["chat"])

SCOPES = ("document", "project", "summary")


def _validate_scope(db: Session, scope: str, scope_id: int) -> None:
    if scope not in SCOPES:
        raise HTTPException(404, f"unknown chat scope {scope!r}")
    if scope == "document" and db.get(Document, scope_id) is None:
        raise HTTPException(404, "Document not found")
    if scope == "project" and db.get(Project, scope_id) is None:
        raise HTTPException(404, "Project not found")
    if scope == "summary" and scope_id != 0:
        raise HTTPException(404, "the summary chat has a single conversation: id 0")


def _message_out(m: ChatMessage) -> dict:
    return {
        "id": m.id,
        "role": m.role,
        "content": m.content,
        "created_at": m.created_at.isoformat()
        if isinstance(m.created_at, datetime)
        else m.created_at,
    }


@router.get("/{scope}/{scope_id}/messages")
def list_messages(scope: str, scope_id: int, db: Session = Depends(get_db)):
    _validate_scope(db, scope, scope_id)
    return [_message_out(m) for m in service.history(db, scope, scope_id)]


@router.delete("/{scope}/{scope_id}/messages", status_code=204)
def clear_messages(scope: str, scope_id: int, db: Session = Depends(get_db)):
    _validate_scope(db, scope, scope_id)
    service.clear(db, scope, scope_id)


class QuestionIn(BaseModel):
    content: str = Field(min_length=1, max_length=4000)


@router.post("/{scope}/{scope_id}/messages")
def ask(
    scope: str, scope_id: int, body: QuestionIn, db: Session = Depends(get_db)
):
    _validate_scope(db, scope, scope_id)
    if not settings.chat_enabled:
        raise HTTPException(503, "chat is disabled (STEELOPTIMA_CHAT_ENABLED)")
    client = OllamaVlmClient()
    model = settings.effective_chat_model
    if not client.text_available(model):
        raise HTTPException(
            503,
            f"chat model {model!r} is not available — is Ollama running "
            f"at {settings.ollama_url}?",
        )
    return StreamingResponse(
        service.stream_answer(db, scope, scope_id, body.content, client),
        media_type="text/plain; charset=utf-8",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )
