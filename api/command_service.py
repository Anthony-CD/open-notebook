from typing import Any, Dict, List, Optional

from loguru import logger
from surreal_commands import get_command_status, submit_command

from open_notebook.database.repository import ensure_record_id, repo_query
from open_notebook.exceptions import InvalidInputError, NotFoundError
from open_notebook.utils.job_status import (
    CANCELED,
    QUEUED,
    normalize_job_status,
    sanitize_job_error,
)

CANCELED_BY_USER_MESSAGE = "Canceled by user"


def _command_record_id(job_id: str):
    """Parse a job id and make sure it points at the ``command`` table.

    The cancel path issues an UPDATE against the given record id, so it must
    never accept ids of other tables (sources, notes, ...).
    """
    try:
        record_id = ensure_record_id(job_id)
    except Exception:
        raise InvalidInputError("Invalid job id")
    if getattr(record_id, "table_name", None) != "command":
        raise InvalidInputError("Invalid job id")
    return record_id


class CommandService:
    """Generic service layer for command operations"""

    @staticmethod
    async def submit_command_job(
        module_name: str,  # Actually app_name for surreal-commands
        command_name: str,
        command_args: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Submit a generic command job for background processing"""
        try:
            # Ensure command modules are imported before submitting
            # This is needed because submit_command validates against local registry
            try:
                import commands.podcast_commands  # noqa: F401
            except ImportError as import_err:
                logger.error(f"Failed to import command modules: {import_err}")
                raise ValueError("Command modules not available")

            # surreal-commands expects: submit_command(app_name, command_name, args)
            cmd_id = submit_command(
                module_name,  # This is actually the app name (e.g., "open_notebook")
                command_name,  # Command name (e.g., "generate_podcast")
                command_args,  # Input data
            )
            # Convert RecordID to string if needed
            if not cmd_id:
                raise ValueError("Failed to get cmd_id from submit_command")
            cmd_id_str = str(cmd_id)
            logger.info(
                f"Submitted command job: {cmd_id_str} for {module_name}.{command_name}"
            )
            return cmd_id_str

        except Exception as e:
            logger.error(f"Failed to submit command job: {e}")
            raise

    @staticmethod
    async def get_command_status(job_id: str) -> Dict[str, Any]:
        """Get status of any command job"""
        try:
            status = await get_command_status(job_id)
            return {
                "job_id": job_id,
                "status": normalize_job_status(status.status if status else None),
                "result": status.result if status else None,
                "error_message": sanitize_job_error(
                    getattr(status, "error_message", None) if status else None
                ),
                "created": str(status.created)
                if status and hasattr(status, "created") and status.created
                else None,
                "updated": str(status.updated)
                if status and hasattr(status, "updated") and status.updated
                else None,
                "progress": getattr(status, "progress", None) if status else None,
            }
        except Exception as e:
            logger.error(f"Failed to get command status: {e}")
            raise

    @staticmethod
    async def list_command_jobs(
        module_filter: Optional[str] = None,
        command_filter: Optional[str] = None,
        status_filter: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """List command jobs with optional filtering"""
        # This will be implemented with proper SurrealDB queries
        # For now, return empty list as this is foundation phase
        return []

    @staticmethod
    async def cancel_command_job(job_id: str) -> Dict[str, Any]:
        """Cancel a queued command job.

        surreal_commands has no cancellation API and no way to interrupt a
        job that is already executing, so only jobs still waiting in the
        queue (raw status ``new`` -> public ``queued``) can be canceled. The
        record is flipped to ``canceled`` with a conditional UPDATE (only
        while still ``new``) so a job that a worker picks up concurrently is
        never marked canceled while it runs. The worker-side guard in
        ``commands/cancel_guard.py`` skips execution of canceled records that
        a worker had already received but not yet started.

        Returns ``{"job_id", "status", "canceled"}``; ``canceled`` is False
        when the job is running or already finished (status says which).
        Canceling an already-canceled job is a no-op that reports success.
        """
        record_id = _command_record_id(job_id)
        rows = await repo_query("SELECT id, status FROM $id", {"id": record_id})
        if not rows:
            raise NotFoundError("Job not found")

        current = normalize_job_status(rows[0].get("status"))
        if current == CANCELED:
            return {"job_id": job_id, "status": CANCELED, "canceled": True}
        if current != QUEUED:
            return {"job_id": job_id, "status": current, "canceled": False}

        updated = await repo_query(
            "UPDATE $id SET status = 'canceled', error_message = $message "
            "WHERE status = 'new' RETURN AFTER",
            {"id": record_id, "message": CANCELED_BY_USER_MESSAGE},
        )
        if updated:
            logger.info(f"Canceled queued command job: {job_id}")
            return {"job_id": job_id, "status": CANCELED, "canceled": True}

        # Lost the race against a worker: report the state it is in now.
        rows = await repo_query("SELECT id, status FROM $id", {"id": record_id})
        current = normalize_job_status(rows[0].get("status") if rows else None)
        return {
            "job_id": job_id,
            "status": current,
            "canceled": current == CANCELED,
        }
