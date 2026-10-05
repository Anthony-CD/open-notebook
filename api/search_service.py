"""Service layer for global search (api/routers/search.py).

Adds the one genuinely missing piece of global search coverage: chat
conversations. Everything else P1 "Organizacion y busqueda" asked to check
turned out to already be covered:

- Sources and notes: open_notebook.domain.notebook.text_search()/
  vector_search() (unchanged here, just reused).
- "Transformation results": the only transformation output that is ever
  persisted is a `source_insight` row (POST /sources/{id}/insights) - a
  one-off POST /transformations/execute result is never saved anywhere
  (api/routers/transformations.py's execute_transformation() returns it
  without calling .save()), so there is nothing to index for that case.
  source_insight rows themselves are already included in both
  text_search()/vector_search() (see open_notebook/database/migrations/
  4.surrealql and 9.surrealql) - no new code needed.

Chat messages are the real gap: ChatSession (open_notebook/domain/
notebook.py) only stores a `title`/`model_override` in SurrealDB - actual
message history lives in LangGraph's SqliteSaver checkpoint
(LANGGRAPH_CHECKPOINT_FILE, see open_notebook/graphs/chat.py), a different
storage engine with no full-text index of its own. This module adds a
best-effort, bounded, case-insensitive substring search over that store -
intentionally NOT a ranked/highlighted search like fn::text_search(), and
clearly labeled as such everywhere it surfaces (SearchRequest.search_chats
docstring, frontend hint text).
"""

import asyncio
from typing import Any, Dict, List

from langchain_core.runnables import RunnableConfig
from loguru import logger

from open_notebook.database.repository import ensure_record_id, repo_query

# Bounds worst-case scan cost. This is a single-user, self-hosted app (see
# docs/7-DEVELOPMENT/architecture.md) - a full scan within these bounds is an
# acceptable trade-off, not a hidden performance cliff, and is the same
# "fetch everything in the window, aggregate in Python" trade-off
# open_notebook/domain/usage.py already makes for usage_event rows.
MAX_CHAT_SESSIONS_SCANNED = 200
MAX_CHAT_MATCHES_PER_SESSION = 3
MAX_SNIPPET_CHARS = 240


def _message_text(msg: Any) -> str:
    """Extract plain text content from a LangChain message object.

    Deliberately NOT importing api.routers._chat_shared.extract_chat_messages
    here (that module is owned by the chat-citations work happening in
    parallel on this same branch) - this is the same one-line extraction,
    duplicated to avoid any cross-agent file coupling.
    """
    content = getattr(msg, "content", None)
    if content is None:
        return str(msg)
    if isinstance(content, str):
        return content
    return str(content)


async def _notebook_ids_for_sessions(session_ids: List[str]) -> Dict[str, str]:
    """Best-effort {session_id: notebook_id} map for a batch of chat sessions.

    A chat_session's `refers_to` edge points at either a notebook (the
    common case, notebook-level chat) or a source (source-level chat, see
    api/routers/source_chat.py); for the latter, one more hop through the
    source's own `reference` edge resolves its notebook. Two batched queries
    total, never N+1. Sessions that resolve to neither (orphaned relation)
    are simply absent from the result - the caller treats that as "can't
    deep-link, show the result without a link".
    """
    if not session_ids:
        return {}
    try:
        direct = await repo_query(
            "SELECT in AS session_id, out AS target FROM refers_to WHERE in IN $ids",
            {"ids": [ensure_record_id(sid) for sid in session_ids]},
        )
    except Exception as e:
        logger.warning(f"Chat search: failed to resolve session parents: {e}")
        return {}

    notebook_by_session: Dict[str, str] = {}
    source_targets: Dict[str, str] = {}  # session_id -> source_id
    for row in direct or []:
        session_id = str(row.get("session_id"))
        target = row.get("target")
        if target is None:
            continue
        target_str = str(target)
        if target_str.startswith("notebook:"):
            notebook_by_session[session_id] = target_str
        elif target_str.startswith("source:"):
            source_targets[session_id] = target_str

    if source_targets:
        try:
            via_source = await repo_query(
                "SELECT in AS source_id, out AS notebook_id FROM reference WHERE in IN $ids",
                {"ids": [ensure_record_id(sid) for sid in source_targets.values()]},
            )
        except Exception as e:
            logger.warning(f"Chat search: failed to resolve source->notebook: {e}")
            via_source = []
        notebook_by_source = {
            str(row.get("source_id")): str(row.get("notebook_id"))
            for row in (via_source or [])
            if row.get("notebook_id")
        }
        for session_id, source_id in source_targets.items():
            notebook_id = notebook_by_source.get(source_id)
            if notebook_id:
                notebook_by_session[session_id] = notebook_id

    return notebook_by_session


async def search_chat_messages(keyword: str, results: int) -> List[Dict[str, Any]]:
    """Best-effort case-insensitive substring search over chat messages.

    Returns rows shaped like text_search()/vector_search() output (id,
    title, content, parent_id, relevance) plus a `matches` list and
    `result_type`/`notebook_id` the frontend uses to render and link chat
    hits distinctly from source/note hits. relevance is a constant 1.0 for
    every hit - an honest "found" rather than a fabricated ranking score.
    """
    if not keyword:
        return []
    needle = keyword.lower()

    try:
        sessions = await repo_query(
            "SELECT id, title FROM chat_session ORDER BY updated DESC LIMIT $limit",
            {"limit": MAX_CHAT_SESSIONS_SCANNED},
        )
    except Exception as e:
        logger.warning(f"Chat search: failed to list chat sessions: {e}")
        return []

    if not sessions:
        return []

    try:
        from open_notebook.graphs.chat import graph as chat_graph
    except Exception as e:
        logger.warning(f"Chat search: chat graph unavailable: {e}")
        return []

    session_ids = [str(row.get("id")) for row in sessions if row.get("id")]
    notebook_by_session = await _notebook_ids_for_sessions(session_ids)

    matches: List[Dict[str, Any]] = []
    for row in sessions:
        if len(matches) >= results:
            break
        session_id = str(row.get("id"))
        title = row.get("title") or "Chat"

        try:
            thread_state = await asyncio.to_thread(
                chat_graph.get_state,
                config=RunnableConfig(configurable={"thread_id": session_id}),
            )
        except Exception as e:
            logger.debug(f"Chat search: could not load state for {session_id}: {e}")
            continue

        if not thread_state or not thread_state.values:
            continue
        raw_messages = thread_state.values.get("messages") or []
        if not raw_messages:
            continue

        session_matches: List[str] = []
        for msg in raw_messages:
            text = _message_text(msg)
            if needle in text.lower():
                snippet = (
                    text if len(text) <= MAX_SNIPPET_CHARS
                    else text[:MAX_SNIPPET_CHARS] + "…"
                )
                session_matches.append(snippet)
                if len(session_matches) >= MAX_CHAT_MATCHES_PER_SESSION:
                    break

        if session_matches:
            matches.append(
                {
                    "id": session_id,
                    "title": title,
                    "content": session_matches[0],
                    "parent_id": session_id,
                    "relevance": 1.0,
                    "matches": session_matches,
                    "result_type": "chat_session",
                    "notebook_id": notebook_by_session.get(session_id),
                }
            )

    return matches
