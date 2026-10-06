"""
Audit log domain model (migration 34 - originally authored as 33, see
open_notebook/database/migrations/33.surrealql's header for why).

Records sensitive actions (deletions, credential add/remove, Drive
connect/disconnect, data export/delete, failed login attempts) for the
activity-history / privacy-center feature (P2 "Seguridad").

This is deliberately NOT a per-user audit trail. Open Notebook is
single-user/household-scale with one shared OPEN_NOTEBOOK_PASSWORD - there
is no per-user identity anywhere in this app to attribute an action to
(see AGENTS.md, docs/7-DEVELOPMENT/decisions/PDR-001-single-user-first.md).
This is a shared household log: whoever is logged in can see every action,
same as everything else in this app.

`action` is a short canonical code (see api/audit_service.py ACTION_*
constants) and `detail` is optional, short, non-sensitive context (a title,
a count, a provider name) - NEVER full content of what was deleted/exported
and NEVER secret values (API keys, OAuth tokens, the shared password).
Writing an audit entry must never break the action it is recording -
api/audit_service.py.record_audit_event() swallows its own failures.
"""

from typing import ClassVar, Optional

from open_notebook.domain.base import ObjectModel


class AuditLog(ObjectModel):
    table_name: ClassVar[str] = "audit_log"

    action: str
    detail: Optional[str] = None
