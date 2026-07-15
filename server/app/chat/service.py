"""Scoped Q&A over the operator's own numbers.

The model is given the scope's data as JSON and is told to answer from it
alone. Same standing rule as everywhere else in this project: geometry (and
OCR, and the optimizer) measure; the model only reads the results back. It is
never asked to compute, so a wrong answer is at worst a misreading the
operator can check against the table on screen — the numbers themselves come
from code.
"""

from collections.abc import Iterator

from sqlalchemy.orm import Session

from app.chat import context as ctx
from app.config import settings
from app.db.models import ChatMessage
from app.vlm.client import OllamaVlmClient

SYSTEM_PROMPT = """\
You are the assistant inside SteelOptima, a tool that reads steel fabrication \
drawings and material tables and builds bills of materials, bids and stock orders.

After these instructions comes a JSON block: the ONLY data you know. Rules:
- Answer ONLY from that JSON. If the answer is not in it, say plainly that the \
data does not contain it. NEVER guess, estimate, or invent a number.
- Quote numbers exactly as they appear, with units (mm, kg). Prices have no \
currency in the data — call them "price units".
- Do not do arithmetic beyond trivial reading of the data. If asked to compute \
something new, point to the closest number the data already has.
- Be brief and concrete. Answer in the language the question was asked in \
(Hebrew questions get Hebrew answers).

Glossary for the JSON:
- Sizes are real-world after applying the sheet scale; scale = real_mm/paper_mm. \
A page whose scale is not confirmed has UNTRUSTWORTHY dimensions - say so when \
asked about sizes from such a page.
- Cutout/table row status: needs_review rows are EXCLUDED from all totals; \
auto_approved passed machine checks; approved/edited passed a human.
- cut_length is the burn distance of a cutout, what cutting costs.
- Order plans are 1D cutting-stock results: "order" lists bars to buy; waste_pct \
is bought-minus-used; infeasible_lengths_mm are pieces longer than any stock and \
NOT covered by the plan - always mention them if present.
- not_included_in_numbers lists work still pending review; totals may grow once \
it is reviewed.
"""


def history(db: Session, scope: str, scope_id: int) -> list[ChatMessage]:
    return (
        db.query(ChatMessage)
        .filter(ChatMessage.scope == scope, ChatMessage.scope_id == scope_id)
        .order_by(ChatMessage.id)
        .all()
    )


def clear(db: Session, scope: str, scope_id: int) -> int:
    n = (
        db.query(ChatMessage)
        .filter(ChatMessage.scope == scope, ChatMessage.scope_id == scope_id)
        .delete()
    )
    db.commit()
    return n


def build_context(db: Session, scope: str, scope_id: int) -> str | None:
    if scope == "document":
        return ctx.document_context(db, scope_id)
    if scope == "project":
        return ctx.project_context(db, scope_id)
    return ctx.summary_context(db)


def stream_answer(
    db: Session,
    scope: str,
    scope_id: int,
    question: str,
    client: OllamaVlmClient | None = None,
) -> Iterator[str]:
    """Yield the answer as it streams; persist both turns of the conversation.

    The context is rebuilt fresh on every question — the operator may have
    approved rows or re-priced materials since the last one.
    """
    client = client or OllamaVlmClient()
    context = build_context(db, scope, scope_id)
    model = settings.effective_chat_model

    # replay recent turns so follow-up questions ("and its weight?") work
    prior = history(db, scope, scope_id)[-settings.chat_history_messages :]
    messages = [
        {"role": "system", "content": f"{SYSTEM_PROMPT}\nDATA:\n{context}"},
        *[{"role": m.role, "content": m.content} for m in prior],
        {"role": "user", "content": question},
    ]

    db.add(
        ChatMessage(
            scope=scope, scope_id=scope_id, role="user", content=question
        )
    )
    db.commit()

    parts: list[str] = []
    error: str | None = None
    try:
        for delta in client.chat_stream(
            messages,
            model=model,
            num_ctx=settings.chat_num_ctx,
            timeout_s=settings.chat_timeout_s,
        ):
            parts.append(delta)
            yield delta
    except Exception as e:  # persist what streamed; the client saw it already
        error = f"{type(e).__name__}: {e}"
        if not parts:
            raise
    finally:
        answer = "".join(parts)
        if error:
            answer += f"\n[interrupted: {error}]"
        if answer:
            db.add(
                ChatMessage(
                    scope=scope,
                    scope_id=scope_id,
                    role="assistant",
                    content=answer,
                    model=model,
                    context_chars=len(context or ""),
                )
            )
            db.commit()
