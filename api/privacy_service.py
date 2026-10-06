"""
Privacy center service (P2 "Seguridad").

Business logic for GET /api/privacy/export and DELETE /api/privacy/data -
api/routers/privacy.py stays a thin HTTP layer per
open_notebook/AGENTS.md's "routes -> services -> models".

Scope, documented here because it's a deliberate judgment call (the task
left "exact shape" to the implementer):

Export bundles content *metadata and text*, not binary blobs: notebook/
source/note/chat-session/study-set/episode records including their text
fields (titles, full_text, note content - this is the user's actual
research data, not incidental). It never inlines uploaded file bytes or
podcast audio - those are referenced by their on-disk path/filename only
(`file_path` / `audio_file`), since inlining them as base64 could make a
modest-looking JSON response balloon to the size of every file this
instance has ever ingested. Credential and Drive-connection entries are
metadata-only (provider/name/email, never the API key or OAuth tokens -
same guarantee api/credentials_service.py gives every other response).

Delete is a FULL WIPE of this instance's content - not scoped to "the
current user" because there is no such thing to scope to (single shared
password, see PDR-001). It removes:
  - notebooks, sources (+ their embeddings/insights), notes, chat sessions
    and chat feedback, study sets, podcast episodes
  - uploaded files on disk (UPLOADS_FOLDER) and podcast audio (PODCASTS_FOLDER)
  - the LangGraph chat-checkpoint SQLite file (chat message history itself
    lives there, not in SurrealDB - see open_notebook/AGENTS.md)
It deliberately does NOT remove: AI provider credentials/models, episode/
speaker profiles, transformations, usage/cost history, or the Drive OAuth
connection - those are configuration the household set up, not research
data, and wiping them on every "delete my data" click would force
re-entering API keys for an action framed as "forget what I researched".
A future per-notebook-scoped delete could reuse Notebook.delete(
delete_exclusive_sources=True) instead of this full wipe, if a lighter
option turns out to be wanted.
"""

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from loguru import logger

from api.audit_service import (
    ACTION_DATA_DELETED,
    ACTION_DATA_EXPORTED,
    record_audit_event,
)
from api.credentials_service import credential_to_response
from open_notebook.config import (
    LANGGRAPH_CHECKPOINT_FILE,
    PODCASTS_FOLDER,
    UPLOADS_FOLDER,
)
from open_notebook.database.repository import repo_query
from open_notebook.domain.credential import Credential
from open_notebook.domain.drive_connection import DriveConnection

REQUIRED_DELETE_CONFIRMATION = "DELETE ALL MY DATA"

# Content tables wiped by delete_all_data(), in an order that doesn't
# matter for correctness (every row goes either way) but deletes the
# smaller/dependent-looking tables first for a tidier failure point if one
# table errors partway through.
_CONTENT_TABLES = [
    "source_embedding",
    "source_insight",
    "chat_feedback",
    "reference",
    "artifact",
    "refers_to",
    "source",
    "note",
    "chat_session",
    "study_set",
    "episode",
    "notebook",
]


async def _count(table: str) -> int:
    result = await repo_query(f"SELECT count() AS total FROM {table} GROUP ALL")
    if result and isinstance(result[0], dict):
        return int(result[0].get("total") or 0)
    return 0


async def build_export() -> Dict[str, Any]:
    """Gather the export bundle. See module docstring for scope."""
    notebooks = await repo_query(
        "SELECT id, name, description, archived, created, updated FROM notebook"
    )
    sources = await repo_query(
        "SELECT id, title, topics, asset.file_path AS file_path, "
        "asset.url AS url, full_text, archived, created, updated "
        "FROM source"
    )
    notes = await repo_query(
        "SELECT id, title, note_type, content, created, updated FROM note"
    )
    chat_sessions = await repo_query(
        "SELECT id, title, created, updated FROM chat_session"
    )
    study_sets = await repo_query("SELECT * FROM study_set")
    episodes = await repo_query(
        "SELECT id, name, briefing, audio_file, created, updated FROM episode"
    )

    credentials: List[Dict[str, Any]] = []
    try:
        for cred in await Credential.get_all(order_by="provider, created"):
            resp = credential_to_response(cred, 0)
            credentials.append(resp.model_dump())
    except Exception as e:
        logger.warning(f"Privacy export: could not list credentials: {e}")

    drive_connection: Optional[Dict[str, Any]] = None
    try:
        conn = await DriveConnection.get_instance()
        if conn.is_connected or conn.account_email:
            drive_connection = {
                "account_email": conn.account_email,
                "connected_at": str(conn.connected_at) if conn.connected_at else None,
                "granted_scopes": conn.granted_scopes,
                "last_sync_at": str(conn.last_sync_at) if conn.last_sync_at else None,
                "needs_reconnect": conn.needs_reconnect,
            }
    except Exception as e:
        logger.warning(f"Privacy export: could not read Drive connection: {e}")

    await record_audit_event(
        ACTION_DATA_EXPORTED,
        detail=f"{len(notebooks)} notebooks, {len(sources)} sources",
    )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "note": (
            "Uploaded files and podcast audio are referenced by their "
            "on-disk file_path/audio_file, not included inline."
        ),
        "notebooks": notebooks,
        "sources": sources,
        "notes": notes,
        "chat_sessions": chat_sessions,
        "study_sets": study_sets,
        "episodes": episodes,
        "credentials": credentials,
        "drive_connection": drive_connection,
    }


def _delete_folder_contents(folder: str) -> int:
    """Delete every file directly under `folder` (non-recursive is wrong
    here - podcast episodes nest audio under per-episode subdirectories -
    so this walks recursively but leaves the folder structure itself."""
    removed = 0
    if not os.path.isdir(folder):
        return removed
    for root, _dirs, files in os.walk(folder):
        for name in files:
            try:
                os.unlink(os.path.join(root, name))
                removed += 1
            except OSError as e:
                logger.warning(f"Privacy delete: could not remove {name}: {e}")
    return removed


async def delete_all_data(confirm: str) -> Dict[str, int]:
    """Full content wipe. Raises ValueError if `confirm` doesn't match the
    required literal phrase - callers map that to a 400."""
    if confirm != REQUIRED_DELETE_CONFIRMATION:
        raise ValueError(
            f'Confirmation phrase must be exactly "{REQUIRED_DELETE_CONFIRMATION}"'
        )

    # Count before deleting so the response can tell the user what actually
    # existed (not just "done").
    counts: Dict[str, int] = {}
    for table in _CONTENT_TABLES:
        counts[table] = await _count(table)

    # Log to the audit trail BEFORE executing, so a crash mid-wipe still
    # leaves a record that a wipe was requested (required by the task spec).
    await record_audit_event(
        ACTION_DATA_DELETED,
        detail=f"full wipe requested: {sum(counts.values())} rows across "
        f"{len(_CONTENT_TABLES)} tables",
    )

    for table in _CONTENT_TABLES:
        try:
            await repo_query(f"DELETE FROM {table}")
        except Exception as e:
            logger.error(f"Privacy delete: failed to clear table {table}: {e}")
            raise

    deleted_files = _delete_folder_contents(UPLOADS_FOLDER)
    deleted_audio = _delete_folder_contents(PODCASTS_FOLDER)
    counts["uploaded_files"] = deleted_files
    counts["podcast_audio_files"] = deleted_audio

    try:
        if os.path.isfile(LANGGRAPH_CHECKPOINT_FILE):
            os.unlink(LANGGRAPH_CHECKPOINT_FILE)
            counts["chat_checkpoint_file"] = 1
        else:
            counts["chat_checkpoint_file"] = 0
    except OSError as e:
        logger.warning(f"Privacy delete: could not remove checkpoint file: {e}")
        counts["chat_checkpoint_file"] = 0

    return counts
