"""Worker-side guard that skips commands canceled while still queued.

surreal_commands has no cancellation support. Its worker receives every new
``command`` record via a LIVE query (or the startup ``status = 'new'`` scan)
and, once a concurrency slot is free, calls ``CommandService.execute_command``,
which unconditionally sets the record to ``running``. A job canceled through
``POST /api/commands/jobs/{id}/cancel`` while it waited for a slot would
therefore still run and overwrite its ``canceled`` status.

``install_cancel_guard()`` wraps ``execute_command`` so it re-reads the record
right before execution and skips it if it is already ``canceled``. Jobs that
are already running cannot be interrupted.
"""

from __future__ import annotations

import functools
from typing import Any

from loguru import logger

from open_notebook.utils.job_status import CANCELED, normalize_job_status

_INSTALLED_ATTR = "__open_notebook_cancel_guard__"


async def _is_canceled(command_id: Any) -> bool:
    from surreal_commands.repository import ensure_record_id, repo_query

    try:
        rows = await repo_query(
            "SELECT status FROM $id", {"id": ensure_record_id(command_id)}
        )
    except Exception as e:  # never block execution because of the guard
        logger.warning(f"Cancel guard could not read command {command_id}: {e}")
        return False
    if not rows:
        return False
    row = rows[0] if isinstance(rows[0], dict) else {}
    return normalize_job_status(row.get("status")) == CANCELED


def install_cancel_guard() -> bool:
    """Patch surreal_commands' CommandService.execute_command once.

    Returns True if the guard is (now) installed, False if surreal_commands'
    internals are not what we expect (the worker then behaves as before).
    """
    try:
        from surreal_commands.core.service import CommandService
    except Exception as e:  # pragma: no cover - defensive
        logger.warning(f"Cancel guard not installed: {e}")
        return False

    original = getattr(CommandService, "execute_command", None)
    if original is None:
        return False
    if getattr(original, _INSTALLED_ATTR, False):
        return True

    @functools.wraps(original)
    async def guarded_execute_command(self, command_id, *args, **kwargs):
        if await _is_canceled(command_id):
            logger.info(f"Skipping command {command_id}: canceled before start")
            return None
        return await original(self, command_id, *args, **kwargs)

    setattr(guarded_execute_command, _INSTALLED_ATTR, True)
    CommandService.execute_command = guarded_execute_command  # type: ignore[method-assign]
    return True
