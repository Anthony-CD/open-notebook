"""
Activity history router (P2 "Seguridad").

Thin HTTP layer over api.audit_service for GET /api/activity - a
paginated, filterable view of the audit log (deletions, credential
add/remove, Drive connect/disconnect, data export/delete, failed logins).

Not a per-user activity feed: this app has exactly one shared login (see
open_notebook/domain/audit_log.py) - every entry is visible to anyone
logged in, same as everything else.
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from loguru import logger

from api.audit_service import list_audit_events
from api.models import ActivityItemResponse, ActivityListResponse
from open_notebook.exceptions import OpenNotebookError

router = APIRouter(tags=["activity"])


@router.get("/activity", response_model=ActivityListResponse)
async def get_activity(
    action: Optional[str] = Query(None, description="Filter by action code"),
    since: Optional[datetime] = Query(
        None, description="Only entries created at/after this ISO datetime"
    ),
    until: Optional[datetime] = Query(
        None, description="Only entries created at/before this ISO datetime"
    ),
    limit: int = Query(50, ge=1, le=200, description="Page size"),
    offset: int = Query(0, ge=0, description="Items to skip"),
):
    """List audit log entries, newest first."""
    try:
        items, total = await list_audit_events(
            action=action, since=since, until=until, limit=limit, offset=offset
        )
        return ActivityListResponse(
            items=[ActivityItemResponse(**item) for item in items],
            total=total,
            limit=limit,
            offset=offset,
        )
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error listing activity: {e}")
        raise HTTPException(status_code=500, detail="Failed to list activity")
