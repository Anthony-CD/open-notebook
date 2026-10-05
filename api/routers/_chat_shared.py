"""Shared helpers for the chat and source-chat routers.

Both `api/routers/chat.py` and `api/routers/source_chat.py` operate on
`chat_session` records linked to their parent (notebook or source) via the
`refers_to` relation, and both convert LangGraph state messages into API
response models. This module holds the single definition of those pieces.

Behavior notes:
- The helpers raise exactly what the previously inlined blocks raised
  (`NotFoundError` propagates from `ObjectModel.get`, `HTTPException(404)` for
  a missing relation), so each router's existing try/except arms keep mapping
  them to the same status codes and messages as before.
"""

from typing import Any, Dict, Iterable, List, Literal, Optional, Tuple

from fastapi import HTTPException
from pydantic import BaseModel, Field

from api.models import Citation
from open_notebook.database.repository import ensure_record_id, repo_query
from open_notebook.domain.notebook import ChatSession, Source


# Shared response models
class ChatMessage(BaseModel):
    id: str = Field(..., description="Message ID")
    type: str = Field(..., description="Message type (human|ai)")
    content: str = Field(..., description="Message content")
    timestamp: Optional[str] = Field(None, description="Message timestamp")
    citations: Optional[List[Citation]] = Field(
        None,
        description=(
            "Structured citations backing this message's [n] markers, read "
            "from the persisted message's additional_kwargs. None for human "
            "messages or AI messages that cited nothing."
        ),
    )
    feedback: Optional[Literal["up", "down"]] = Field(
        None, description="This message's stored thumbs up/down rating, if any"
    )


class SuccessResponse(BaseModel):
    success: bool = Field(True, description="Operation success status")
    message: str = Field(..., description="Success message")


def normalize_record_id(table: str, record_id: str) -> str:
    """Ensure a record ID carries its table prefix (`table:id`)."""
    prefix = f"{table}:"
    return record_id if record_id.startswith(prefix) else f"{prefix}{record_id}"


async def get_source_or_404(source_id: str) -> Tuple[str, Source]:
    """Normalize a source ID and fetch the source, 404 if missing."""
    full_source_id = normalize_record_id("source", source_id)
    source = await Source.get(full_source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    return full_source_id, source


async def get_session_or_404(session_id: str) -> Tuple[str, ChatSession]:
    """Normalize a session ID and fetch the chat session, 404 if missing."""
    full_session_id = normalize_record_id("chat_session", session_id)
    session = await ChatSession.get(full_session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return full_session_id, session


async def get_verified_source_session(
    source_id: str, session_id: str
) -> Tuple[str, Source, str, ChatSession]:
    """Verify the source exists, the session exists, and the session refers to
    the source. Returns the normalized IDs plus both records."""
    full_source_id, source = await get_source_or_404(source_id)
    full_session_id, session = await get_session_or_404(session_id)

    relation_query = await repo_query(
        "SELECT * FROM refers_to WHERE in = $session_id AND out = $source_id",
        {
            "session_id": ensure_record_id(full_session_id),
            "source_id": ensure_record_id(full_source_id),
        },
    )
    if not relation_query:
        raise HTTPException(status_code=404, detail="Session not found for this source")

    return full_source_id, source, full_session_id, session


def extract_chat_messages(
    raw_messages: Iterable[Any],
    feedback_map: Optional[Dict[str, str]] = None,
) -> List[ChatMessage]:
    """Convert LangGraph/LangChain state messages into `ChatMessage` models.

    Citations are read from each message's `additional_kwargs["citations"]`
    (stashed there by the chat/source_chat graphs when the AI message is
    built) - this is how citations survive a checkpoint reload, since
    LangGraph persists the whole message object including
    `additional_kwargs`. `feedback_map` (message id -> "up"/"down") comes
    from the separate `chat_feedback` table; pass it in when available so a
    reopened conversation still shows the stored rating.
    """
    feedback_map = feedback_map or {}
    messages: List[ChatMessage] = []
    for msg in raw_messages:
        message_id = getattr(msg, "id", f"msg_{len(messages)}")
        additional_kwargs = getattr(msg, "additional_kwargs", None) or {}
        raw_citations = (
            additional_kwargs.get("citations")
            if isinstance(additional_kwargs, dict)
            else None
        )
        citations = None
        if raw_citations:
            try:
                citations = [Citation(**c) for c in raw_citations]
            except Exception:
                # A malformed/legacy payload shouldn't break message loading.
                citations = None
        messages.append(
            ChatMessage(
                id=message_id,
                type=msg.type if hasattr(msg, "type") else "unknown",
                content=msg.content if hasattr(msg, "content") else str(msg),
                timestamp=None,  # LangChain messages don't have timestamps by default
                citations=citations,
                feedback=feedback_map.get(str(message_id)),
            )
        )
    return messages


class FeedbackRequest(BaseModel):
    rating: Optional[Literal["up", "down"]] = Field(
        None,
        description="Thumbs up/down rating for this message, or null to clear it",
    )


async def get_feedback_map(session_id: str) -> Dict[str, str]:
    """Fetch this session's stored message ratings as {message_id: rating}.

    Defensive about unexpected row shapes (e.g. a differently-shaped mock in
    tests, or a future schema tweak): rows missing the expected fields are
    skipped rather than raising.
    """
    try:
        rows = await repo_query(
            "SELECT message_id, rating FROM chat_feedback WHERE session_id = $session_id",
            {"session_id": session_id},
        )
    except Exception:
        return {}

    feedback_map: Dict[str, str] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        message_id = row.get("message_id")
        rating = row.get("rating")
        if message_id and rating in ("up", "down"):
            feedback_map[str(message_id)] = rating
    return feedback_map


async def set_feedback(
    session_id: str, message_id: str, rating: Optional[str]
) -> None:
    """Upsert (or clear) a message's thumbs up/down rating.

    Keyed by a deterministic record id derived from session+message so a
    repeated rating on the same message updates the same row instead of
    accumulating duplicates.
    """
    record_key = f"{session_id}:{message_id}".replace(":", "_")
    if rating is None:
        await repo_query(
            "DELETE chat_feedback WHERE session_id = $session_id AND message_id = $message_id",
            {"session_id": session_id, "message_id": message_id},
        )
        return

    await repo_query(
        """
        UPSERT type::thing('chat_feedback', $record_key)
        SET session_id = $session_id, message_id = $message_id, rating = $rating,
            updated = time::now()
        """,
        {
            "record_key": record_key,
            "session_id": session_id,
            "message_id": message_id,
            "rating": rating,
        },
    )
