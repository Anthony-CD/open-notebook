from typing import Any, ClassVar, Dict, List, Optional

from loguru import logger
from pydantic import Field

from open_notebook.database.repository import ensure_record_id, repo_query
from open_notebook.domain.base import ObjectModel, RecordModel
from open_notebook.exceptions import DatabaseOperationError


class Transformation(ObjectModel):
    table_name: ClassVar[str] = "transformation"
    nullable_fields: ClassVar[set[str]] = {"model_id"}
    name: str
    title: str
    description: str
    prompt: str
    apply_default: bool
    model_id: Optional[str] = None

    def _prepare_save_data(self) -> Dict[str, Any]:
        data = super()._prepare_save_data()
        if data.get("model_id"):
            data["model_id"] = ensure_record_id(data["model_id"])
        return data

    async def get_result_versions(self, source_id: str) -> List[Dict[str, Any]]:
        """Return every result this transformation has produced on a source.

        ``source_insight`` rows are never overwritten on re-run (each run
        ``CREATE``s a new row via ``create_insight_command`` -
        ``commands/embedding_commands.py``) - prior results are already kept.
        What was missing is a *version* concept to present that history
        coherently instead of a flat, growing list of same-titled insights.

        This is computed read-side, matched on ``source`` + ``insight_type``
        (the existing link - insights don't carry a ``transformation``
        reference today). Migration 29 adds optional ``transformation`` and
        ``version`` fields to ``source_insight`` for a future write path to
        populate explicitly; until something writes them, every row's
        ``version`` is None here and we fall back to the row's 1-based
        position ordered by ``created`` (oldest = version 1, matching "an
        existing un-versioned result becomes version 1 implicitly"). A row
        that *does* carry an explicit ``version`` (future write path) is
        trusted over the computed position.

        Returns oldest-to-newest; the last entry has ``is_latest=True``.
        Raises DatabaseOperationError on query failure.
        """
        try:
            rows = await repo_query(
                """
                SELECT id, content, version, created FROM source_insight
                WHERE source = $source_id AND insight_type = $insight_type
                ORDER BY created ASC
                """,
                {
                    "source_id": ensure_record_id(source_id),
                    "insight_type": self.title,
                },
            )
        except Exception as e:
            logger.error(
                f"Error fetching result versions for transformation {self.id} "
                f"on source {source_id}: {e}"
            )
            raise DatabaseOperationError(
                "Failed to fetch transformation result history"
            )

        versions: List[Dict[str, Any]] = []
        for position, row in enumerate(rows, start=1):
            explicit_version = row.get("version")
            version_number = (
                explicit_version if isinstance(explicit_version, int) else position
            )
            created = row.get("created")
            versions.append(
                {
                    "version": version_number,
                    "content": row.get("content") or "",
                    "created": str(created) if created else None,
                    "is_latest": False,
                }
            )
        if versions:
            versions[-1]["is_latest"] = True
        return versions


class DefaultPrompts(RecordModel):
    record_id: ClassVar[str] = "open_notebook:default_prompts"
    transformation_instructions: Optional[str] = Field(
        None, description="Instructions for executing a transformation"
    )
