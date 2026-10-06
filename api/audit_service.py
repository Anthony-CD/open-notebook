"""
Audit log service.

Business logic for the P2 "Seguridad" audit trail: a small, central place
to record a sensitive action so call sites (routers, middleware) stay a
1-3 line addition, per open_notebook/AGENTS.md's "routers stay thin,
business logic goes in *_service.py". api/routers/activity.py (listing)
and api/routers/privacy.py (export/delete) depend on this module too.

See open_notebook/domain/audit_log.py for what this is NOT (a per-user
trail - this app has no per-user identity, see PDR-001).
"""

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from open_notebook.database.repository import repo_query
from open_notebook.domain.audit_log import AuditLog

# Canonical action codes written by call sites. The frontend never shows
# one of these raw - it maps each to a translated sentence (mirrors
# JobStatusBadge's normalize-then-translate pattern for job statuses).
ACTION_LOGIN_FAILED = "login_failed"
ACTION_NOTEBOOK_DELETED = "notebook_deleted"
ACTION_SOURCE_DELETED = "source_deleted"
ACTION_CREDENTIAL_CREATED = "credential_created"
ACTION_CREDENTIAL_DELETED = "credential_deleted"
ACTION_DRIVE_CONNECTED = "drive_connected"
ACTION_DRIVE_DISCONNECTED = "drive_disconnected"
ACTION_DATA_EXPORTED = "data_exported"
ACTION_DATA_DELETED = "data_deleted"

# Known actions, used by the frontend-label mapping test and to validate the
# `action` filter on GET /api/activity (an unknown value just yields no
# rows - not a 400 - so a frontend/backend version drift never breaks the
# page, but it's still useful to enumerate for the dropdown/tests).
KNOWN_ACTIONS: List[str] = [
    ACTION_LOGIN_FAILED,
    ACTION_NOTEBOOK_DELETED,
    ACTION_SOURCE_DELETED,
    ACTION_CREDENTIAL_CREATED,
    ACTION_CREDENTIAL_DELETED,
    ACTION_DRIVE_CONNECTED,
    ACTION_DRIVE_DISCONNECTED,
    ACTION_DATA_EXPORTED,
    ACTION_DATA_DELETED,
]

# Detail strings are logged verbatim - callers must only ever pass short,
# non-sensitive context (titles, counts, provider names), truncated
# defensively here in case a title is unexpectedly huge.
_MAX_DETAIL_LENGTH = 300


async def record_audit_event(action: str, detail: Optional[str] = None) -> None:
    """Best-effort audit write.

    Never raises: a failure to record an audit entry must not break the
    mutation it's recording (e.g. a notebook delete must still succeed
    even if the audit_log insert fails). Failures are logged instead.
    """
    try:
        safe_detail = detail[:_MAX_DETAIL_LENGTH] if detail else None
        entry = AuditLog(action=action, detail=safe_detail)
        await entry.save()
    except Exception as e:
        logger.warning(f"Failed to record audit event '{action}': {e}")


async def list_audit_events(
    *,
    action: Optional[str] = None,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    limit: int = 50,
    offset: int = 0,
) -> Tuple[List[Dict[str, Any]], int]:
    """Paginated, filterable listing for GET /api/activity, newest first.

    Returns (items, total_count).
    """
    conditions: List[str] = []
    params: Dict[str, Any] = {}
    if action:
        conditions.append("action = $action")
        params["action"] = action
    if since:
        conditions.append("created >= $since")
        params["since"] = since
    if until:
        conditions.append("created <= $until")
        params["until"] = until
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    rows = await repo_query(
        f"SELECT id, action, detail, created FROM audit_log {where} "
        f"ORDER BY created DESC LIMIT $limit START $offset",
        {**params, "limit": limit, "offset": offset},
    )

    count_result = await repo_query(
        f"SELECT count() AS total FROM audit_log {where} GROUP ALL",
        params,
    )
    total = 0
    if count_result and isinstance(count_result[0], dict):
        total = int(count_result[0].get("total") or 0)

    items = [
        {
            "id": str(row.get("id", "")),
            "action": row.get("action", ""),
            "detail": row.get("detail"),
            "created": str(row.get("created")) if row.get("created") else "",
        }
        for row in rows
    ]
    return items, total
