import asyncio
import os
from pathlib import Path
from typing import Any, List, Optional

from content_core import check_file_support
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
)
from fastapi.responses import FileResponse, Response
from loguru import logger
from pydantic import ValidationError
from surreal_commands import execute_command_sync, submit_command

from api.command_service import CommandService
from api.credentials_service import validate_url
from api.middleware import get_max_upload_size_bytes
from api.models import (
    AssetModel,
    CreateSourceInsightRequest,
    InsightCreationResponse,
    SourceCreate,
    SourceDeletePreview,
    SourceInsightResponse,
    SourceListResponse,
    SourceResponse,
    SourceStatusResponse,
    SourceUpdate,
    SourceUploadLimits,
)
from commands.source_commands import SourceProcessingInput
from open_notebook.config import UPLOADS_FOLDER
from open_notebook.database.repository import ensure_record_id, repo_query
from open_notebook.domain.notebook import Asset, Notebook, Source
from open_notebook.domain.transformation import Transformation
from open_notebook.exceptions import (
    InvalidInputError,
    NotFoundError,
    OpenNotebookError,
    UnsupportedTypeException,
)
from open_notebook.utils.content_hash import compute_content_hash
from open_notebook.utils.job_status import (
    ACTIVE_JOB_STATUSES,
    normalize_job_status,
    sanitize_job_error,
)

router = APIRouter()

# Human-readable (English) message per public job status. The frontend
# renders its own localized label from ``status``; this text is only for API
# consumers and must never contain enum reprs or internal error text.
SOURCE_STATUS_MESSAGES = {
    "queued": "Source processing queued",
    "processing": "Source processing in progress",
    "completed": "Source processing completed successfully",
    "failed": "Source processing failed",
    "canceled": "Source processing canceled",
    "unknown": "Source processing status unknown",
}

# Per-source locks so two concurrent retry requests (double click) can't both
# pass the "already processing?" check and enqueue two jobs. Single API
# process; the status check below is the cross-process guard.
_retry_locks: dict[str, asyncio.Lock] = {}


def _retry_lock(source_id: str) -> asyncio.Lock:
    lock = _retry_locks.get(source_id)
    if lock is None:
        lock = _retry_locks[source_id] = asyncio.Lock()
    return lock


async def _assert_file_supported(file_path: str) -> None:
    """Pre-flight check that content-core can actually extract this file.

    Rejects unsupported uploads at ingestion time (HTTP 415) instead of letting
    them enqueue a background job that fails and then burns the full retry
    budget before surfacing a generic error (#975). Uses content-core's
    header-only routing, which is the same logic real extraction uses, so the
    verdict can't disagree with what would happen downstream.

    Unexpected errors (e.g. the file no longer exists on a retry) are swallowed
    so this never turns a transient problem into a hard rejection — real
    extraction will surface those.
    """
    try:
        support = await check_file_support(file_path)
    except Exception as e:  # pragma: no cover - defensive
        logger.debug(f"Pre-flight file-support check skipped for {file_path}: {e}")
        return

    if not support.supported:
        detail = support.reason or "Unsupported file type"
        if support.identified_type:
            detail = f"{detail} (detected type: {support.identified_type})"
        raise UnsupportedTypeException(detail)


def _truncate_error(msg: Optional[str], limit: int = 200) -> Optional[str]:
    """Make error text surfaced to clients safe.

    Command/processing failures can carry arbitrary internal exception text;
    strip tracebacks, redact credentials and cap at ``limit`` characters (see
    ``sanitize_job_error``). ``None``/empty passes through unchanged.
    """
    if not msg:
        return msg
    return sanitize_job_error(msg, limit=limit)


SOURCE_SORT_FIELDS = {
    "created": "created",
    "updated": "updated",
    # `title` carries a SEARCH (BM25) index; SurrealDB's planner tries to use
    # it for ORDER BY and fails ("No iterator has been found"), so we sort by
    # a computed alias instead — which also makes the sort case-insensitive.
    "title": "title_sort",
    "insights_count": "insights_count",
    "embedded": "embedded",
    "type": "type",
}

SOURCE_TYPE_EXPRESSION = (
    "IF asset.file_path != NONE THEN 'file' "
    "ELSE IF asset.url != NONE THEN 'link' ELSE 'text' END"
)


async def _stamp_source_view(source_id: str) -> None:
    # Best-effort write-on-read: recording the view timestamp must never turn a
    # successful read into a 500. Log and move on if the stamp update fails.
    try:
        await repo_query(
            "UPDATE $source_id SET last_viewed_at = time::now();",
            {"source_id": ensure_record_id(source_id)},
        )
    except Exception as e:
        logger.warning(f"Failed to stamp last_viewed_at for source {source_id}: {e}")


async def _get_source_notebook_ids(source_id: str) -> List[str]:
    """Notebooks this source is linked to. `reference` is a graph edge
    (RELATE source->reference->notebook), so it only has `in`/`out` columns."""
    references = await repo_query(
        "SELECT VALUE out FROM reference WHERE in = $source_id",
        {"source_id": ensure_record_id(source_id)},
    )
    return [str(nb_id) for nb_id in references] if references else []


def generate_unique_filename(original_filename: str, upload_folder: str) -> str:
    """Generate unique filename like Streamlit app (append counter if file exists),
    atomically reserving it so two concurrent uploads that land on the same
    candidate name can't both pass the check and then clobber each other -
    the loser's claim attempt fails and moves on to the next candidate."""
    file_path = Path(upload_folder)
    file_path.mkdir(parents=True, exist_ok=True)

    # Strip directory components to prevent path traversal
    safe_filename = os.path.basename(original_filename)
    if not safe_filename:
        raise ValueError("Invalid filename")

    # Split filename and extension
    stem = Path(safe_filename).stem
    suffix = Path(safe_filename).suffix
    safe_root = file_path.resolve()

    # Find and atomically claim a unique name
    counter = 0
    while True:
        if counter == 0:
            new_filename = safe_filename
        else:
            new_filename = f"{stem} ({counter}){suffix}"

        full_path = file_path / new_filename
        # Verify resolved path stays within upload folder
        resolved = full_path.resolve()
        if not str(resolved).startswith(str(safe_root) + os.sep):
            raise ValueError("Invalid filename: path traversal detected")

        try:
            # O_EXCL via touch(exist_ok=False): atomically create-or-fail,
            # instead of exists() (check) followed by a separate write
            # (act) elsewhere with a race window in between.
            resolved.touch(exist_ok=False)
            return str(resolved)
        except FileExistsError:
            counter += 1


def _write_uploaded_file(filename: str, content: bytes) -> tuple[str, str]:
    """Sync filesystem work for save_uploaded_file() - run via asyncio.to_thread
    so a large upload doesn't block the event loop for other requests.

    Returns (file_path, content_hash). The hash is computed from the same
    bytes written to disk, before anything else can touch them, so the
    duplicate pre-check in create_source() always hashes the actual upload.
    """
    file_path = generate_unique_filename(filename, UPLOADS_FOLDER)
    try:
        with open(file_path, "wb") as f:
            f.write(content)

        logger.info(f"Saved uploaded file to: {file_path}")
        return file_path, compute_content_hash(content)
    except Exception as e:
        logger.error(f"Failed to save uploaded file: {e}")
        # Clean up partial file if it exists
        if os.path.exists(file_path):
            os.unlink(file_path)
        raise


async def save_uploaded_file(upload_file: UploadFile) -> tuple[str, str]:
    """Save uploaded file to uploads folder; return (file_path, content_hash)."""
    if not upload_file.filename:
        raise ValueError("No filename provided")

    content = await upload_file.read()
    return await asyncio.to_thread(_write_uploaded_file, upload_file.filename, content)


def parse_source_form_data(
    type: str = Form(...),
    notebook_id: Optional[str] = Form(None),
    notebooks: Optional[str] = Form(None),  # JSON string of notebook IDs
    url: Optional[str] = Form(None),
    content: Optional[str] = Form(None),
    title: Optional[str] = Form(None),
    transformations: Optional[str] = Form(None),  # JSON string of transformation IDs
    embed: str = Form("false"),  # Accept as string, convert to bool
    delete_source: str = Form("false"),  # Accept as string, convert to bool
    async_processing: str = Form("false"),  # Accept as string, convert to bool
    force_duplicate: str = Form("false"),  # Accept as string, convert to bool
    file: Optional[UploadFile] = File(None),
) -> tuple[SourceCreate, Optional[UploadFile]]:
    """Parse form data into SourceCreate model and return upload file separately."""
    import json

    # Convert string booleans to actual booleans
    def str_to_bool(value: str) -> bool:
        return value.lower() in ("true", "1", "yes", "on")

    embed_bool = str_to_bool(embed)
    delete_source_bool = str_to_bool(delete_source)
    async_processing_bool = str_to_bool(async_processing)
    force_duplicate_bool = str_to_bool(force_duplicate)

    # Parse JSON strings
    notebooks_list = None
    if notebooks:
        try:
            notebooks_list = json.loads(notebooks)
        except json.JSONDecodeError:
            logger.error(f"Invalid JSON in notebooks field: {notebooks}")
            raise HTTPException(
                status_code=422, detail="Invalid JSON in notebooks field"
            )

    transformations_list = []
    if transformations:
        try:
            transformations_list = json.loads(transformations)
        except json.JSONDecodeError:
            logger.error(f"Invalid JSON in transformations field: {transformations}")
            raise HTTPException(
                status_code=422, detail="Invalid JSON in transformations field"
            )

    # Create SourceCreate instance
    try:
        source_data = SourceCreate(
            type=type,
            notebook_id=notebook_id,
            notebooks=notebooks_list,
            url=url,
            content=content,
            title=title,
            file_path=None,  # Will be set later if file is uploaded
            transformations=transformations_list,
            embed=embed_bool,
            delete_source=delete_source_bool,
            async_processing=async_processing_bool,
            force_duplicate=force_duplicate_bool,
        )
    except ValidationError as e:
        errors = "; ".join(err.get("msg", "invalid value") for err in e.errors())
        logger.error(f"Invalid source form data: {errors}")
        raise HTTPException(status_code=422, detail=f"Invalid source data: {errors}")
    except Exception as e:
        logger.error(f"Failed to create SourceCreate instance: {e}")
        raise

    return source_data, file


@router.get("/sources", response_model=List[SourceListResponse])
async def get_sources(
    notebook_id: Optional[str] = Query(None, description="Filter by notebook ID"),
    limit: int = Query(
        50, ge=1, le=100, description="Number of sources to return (1-100)"
    ),
    offset: int = Query(0, ge=0, description="Number of sources to skip"),
    sort_by: str = Query(
        "updated",
        description="Field to sort by (type, title, created, updated, insights_count, or embedded)",
    ),
    sort_order: str = Query("desc", description="Sort order (asc or desc)"),
    archived: Optional[bool] = Query(
        None,
        description="Filter by archived status (omit to return both archived and active)",
    ),
):
    """Get sources with pagination and sorting support."""
    try:
        # Validate sort parameters
        if sort_by not in SOURCE_SORT_FIELDS:
            raise HTTPException(
                status_code=400,
                detail=(
                    "sort_by must be one of: type, title, created, updated, "
                    "insights_count, embedded"
                ),
            )
        if sort_order.lower() not in ["asc", "desc"]:
            raise HTTPException(
                status_code=400, detail="sort_order must be 'asc' or 'desc'"
            )

        # Build ORDER BY clause
        order_clause = (
            f"ORDER BY {SOURCE_SORT_FIELDS[sort_by]} {sort_order.upper()}, id ASC"
        )

        # Build the query - same projection with or without the notebook
        # filter; only the FROM clause and bound params differ.
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if notebook_id:
            # Verify notebook exists first
            notebook = await Notebook.get(notebook_id)
            if not notebook:
                raise HTTPException(status_code=404, detail="Notebook not found")

            from_clause = "(select value in from reference where out=$notebook_id)"
            params["notebook_id"] = ensure_record_id(notebook_id)
        else:
            from_clause = "source"

        # Default behavior unchanged (no filter = both archived and active),
        # opt-in like Notebook's own archived filter (api/routers/notebooks.py).
        where_clause = ""
        if archived is not None:
            where_clause = "WHERE (archived OR false) = $archived"
            params["archived"] = archived

        # Query sources - include command field with FETCH
        query = f"""
            SELECT id, asset, created, title, updated, topics, command,
            archived, duplicate_of,
            string::lowercase(title OR '') AS title_sort,
            ({SOURCE_TYPE_EXPRESSION}) AS type,
            (SELECT VALUE count() FROM source_insight WHERE source = $parent.id GROUP ALL)[0].count OR 0 AS insights_count,
            (SELECT VALUE id FROM source_embedding WHERE source = $parent.id LIMIT 1) != [] AS embedded
            FROM {from_clause}
            {where_clause}
            {order_clause}
            LIMIT $limit START $offset
            FETCH command
        """
        result = await repo_query(query, params)

        # Convert result to response model
        # Command data is already fetched via FETCH command clause
        response_list = []
        for row in result:
            command = row.get("command")
            command_id = None
            status = None
            processing_info = None

            # Extract status from fetched command object (already resolved by FETCH)
            if command and isinstance(command, dict):
                command_id = str(command.get("id")) if command.get("id") else None
                status = normalize_job_status(command.get("status"))
                # Extract execution metadata from nested result structure
                result_data = command.get("result")
                execution_metadata = (
                    result_data.get("execution_metadata", {})
                    if isinstance(result_data, dict)
                    else {}
                )
                processing_info = {
                    "started_at": execution_metadata.get("started_at"),
                    "completed_at": execution_metadata.get("completed_at"),
                    "error": _truncate_error(command.get("error_message")),
                }
            elif command:
                # Command exists but FETCH failed to resolve it (broken reference)
                command_id = str(command)
                status = "unknown"

            response_list.append(
                SourceListResponse(
                    id=row["id"],
                    title=row.get("title"),
                    topics=row.get("topics") or [],
                    asset=AssetModel(
                        file_path=row["asset"].get("file_path")
                        if row.get("asset")
                        else None,
                        url=row["asset"].get("url") if row.get("asset") else None,
                    )
                    if row.get("asset")
                    else None,
                    embedded=row.get("embedded", False),
                    embedded_chunks=0,  # Not needed in list view
                    insights_count=row.get("insights_count", 0),
                    created=str(row["created"]),
                    updated=str(row["updated"]),
                    # Status fields from fetched command
                    command_id=command_id,
                    status=status,
                    processing_info=processing_info,
                    archived=bool(row.get("archived")),
                    duplicate_of=str(row["duplicate_of"])
                    if row.get("duplicate_of")
                    else None,
                )
            )

        return response_list
    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error fetching sources: {str(e)}")
        raise HTTPException(status_code=500, detail="Error fetching sources")


# NOTE: must be registered before GET /sources/{source_id} below - both are
# one path segment past /sources, and FastAPI/Starlette matches routes in
# registration order, so a literal "/sources/limits" registered after the
# {source_id} route would be swallowed by it (source_id="limits").
@router.get("/sources/limits", response_model=SourceUploadLimits)
async def get_source_upload_limits():
    """Real, currently-enforced upload limits for the UI to display.

    Single source of truth with api/middleware.py's MaxBodySizeMiddleware -
    the UI's helper text should never hardcode a guess that can drift from
    what the server actually enforces. There is no fixed file-type
    allowlist to report (see SourceUploadLimits docstring).
    """
    max_bytes = get_max_upload_size_bytes()
    return SourceUploadLimits(
        max_upload_size_bytes=max_bytes,
        max_upload_size_mb=round(max_bytes / (1024 * 1024), 2),
    )


def _source_to_response(
    source: Source, embedded_chunks: int = 0, **extras: Any
) -> SourceResponse:
    """Build a SourceResponse from a Source, deriving the shared fields.

    Endpoint-specific fields (command_id, status, processing_info, notebooks,
    file_available, ...) are passed as keyword arguments and override the
    derived values.
    """
    fields: dict[str, Any] = {
        "id": source.id or "",
        "title": source.title,
        "topics": source.topics or [],
        "asset": AssetModel(
            file_path=source.asset.file_path,
            url=source.asset.url,
        )
        if source.asset
        else None,
        "full_text": source.full_text,
        "embedded": embedded_chunks > 0,
        "embedded_chunks": embedded_chunks,
        "created": str(source.created),
        "updated": str(source.updated),
        "archived": bool(source.archived),
        "duplicate_of": str(source.duplicate_of) if source.duplicate_of else None,
    }
    fields.update(extras)
    return SourceResponse(**fields)


def _cleanup_uploaded_file(
    file_path: Optional[str], upload_file: Optional[UploadFile]
) -> None:
    """Best-effort removal of a file this request uploaded, after a failure.

    Only removes files we created ourselves (an upload_file was provided) -
    never a caller-supplied file_path."""
    if file_path and upload_file:
        try:
            os.unlink(file_path)
        except Exception:
            pass


async def _build_content_state(
    source_data: SourceCreate, file_path: Optional[str]
) -> dict[str, Any]:
    """Validate the type-specific input and build the content_state passed to
    the processing command. The SSRF and LFI guards live here."""
    content_state: dict[str, Any] = {}

    if source_data.type == "link":
        if not source_data.url:
            raise HTTPException(status_code=400, detail="URL is required for link type")
        # Block SSRF to internal/metadata addresses before the server ever
        # fetches this URL (same guard used for provider-credential URLs).
        try:
            await validate_url(source_data.url, "source")
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        content_state["url"] = source_data.url
    elif source_data.type == "upload":
        # Use uploaded file path or provided file_path (backward compatibility)
        final_file_path = file_path or source_data.file_path
        if not final_file_path:
            raise HTTPException(
                status_code=400,
                detail="File upload or file_path is required for upload type",
            )
        # Validate file_path is within the uploads directory to prevent LFI
        uploads_resolved = Path(UPLOADS_FOLDER).resolve()
        file_resolved = Path(final_file_path).resolve()
        if not str(file_resolved).startswith(str(uploads_resolved) + os.sep):
            raise HTTPException(
                status_code=400,
                detail="Invalid file path: must be within the uploads directory",
            )
        # Reject unsupported files before enqueueing a doomed background job.
        await _assert_file_supported(final_file_path)
        content_state["file_path"] = final_file_path
        content_state["delete_source"] = source_data.delete_source
    elif source_data.type == "text":
        if not source_data.content:
            raise HTTPException(
                status_code=400, detail="Content is required for text type"
            )
        content_state["content"] = source_data.content
    else:
        raise HTTPException(
            status_code=400,
            detail="Invalid source type. Must be link, upload, or text",
        )

    return content_state


async def _check_duplicate_before_create(
    source_data: SourceCreate, upload_hash: Optional[str]
) -> None:
    """Pre-flight duplicate warning for source types whose content is known
    before the async pipeline runs (text, upload).

    Link sources can't be checked here - their content only exists after
    content-core extracts it - so duplicate detection for links happens
    post-hoc in the source_graph (open_notebook/graphs/source.py
    save_source()) and surfaces via `duplicate_of` on GET instead of
    blocking creation.

    Raises HTTP 409 with structured detail when a duplicate is found and
    the caller hasn't set force_duplicate (the client resubmits the same
    request with force_duplicate=true after the user confirms).
    """
    if source_data.force_duplicate:
        return
    if not source_data.notebooks:
        # Nothing to compare against - a source with no notebook yet can't
        # collide with anything scoped to a notebook.
        return

    content_hash: Optional[str] = None
    if source_data.type == "text" and source_data.content:
        content_hash = compute_content_hash(source_data.content)
    elif source_data.type == "upload" and upload_hash:
        content_hash = upload_hash

    if not content_hash:
        return

    duplicate = await Source.find_duplicate(content_hash, source_data.notebooks)
    if duplicate:
        raise HTTPException(
            status_code=409,
            detail={
                "message": (
                    "A source with identical content already exists in "
                    "this notebook."
                ),
                "duplicate": True,
                "existing_source_id": duplicate.id,
                "existing_source_title": duplicate.title,
                "existing_source_created": str(duplicate.created),
            },
        )


async def _create_source_async_path(
    source_data: SourceCreate,
    content_state: dict[str, Any],
    transformation_ids: List[str],
    file_path: Optional[str],
) -> SourceResponse:
    """ASYNC PATH: Create source record first, then queue command."""
    logger.info("Using async processing path")

    # Create source record with asset - let SurrealDB generate the ID
    # Persist asset before save so it's available for retry if processing fails
    if source_data.type == "link":
        source_asset = Asset(url=source_data.url)
    elif source_data.type == "upload":
        source_asset = Asset(file_path=file_path or source_data.file_path)
    else:
        source_asset = None

    source = Source(
        title=source_data.title or "Processing...",
        topics=[],
        asset=source_asset,
    )
    await source.save()

    # Add source to notebooks immediately so it appears in the UI
    # The source_graph will skip adding duplicates
    for notebook_id in source_data.notebooks or []:
        await source.add_to_notebook(notebook_id)

    try:
        # Import command modules to ensure they're registered
        import commands.source_commands  # noqa: F401

        # Submit command for background processing
        command_input = SourceProcessingInput(
            source_id=str(source.id),
            content_state=content_state,
            notebook_ids=source_data.notebooks,
            transformations=transformation_ids,
            embed=source_data.embed,
        )

        command_id = await CommandService.submit_command_job(
            "open_notebook",  # app name
            "process_source",  # command name
            command_input.model_dump(),
        )

        logger.info(f"Submitted async processing command: {command_id}")

        # Update source with command reference immediately
        # command_id already includes 'command:' prefix
        source.command = ensure_record_id(command_id)
        await source.save()

        # Return source with command info
        return _source_to_response(
            source,
            asset=None,  # Will be populated after processing
            full_text=None,  # Will be populated after processing
            embedded=False,  # Will be updated after processing
            embedded_chunks=0,
            command_id=command_id,
            status="queued",
            processing_info={"async": True, "queued": True},
        )

    except (HTTPException, OpenNotebookError):
        # Clean up source record before the error propagates (typed domain
        # errors are mapped by the global handlers in api/main.py)
        try:
            await source.delete()
        except Exception:
            pass
        raise
    except Exception as e:
        logger.error(f"Failed to submit async processing command: {e}")
        # Clean up source record on command submission failure
        try:
            await source.delete()
        except Exception:
            pass
        # The uploaded file (if any) is cleaned up by create_source's handlers
        raise HTTPException(status_code=500, detail="Failed to queue processing")


async def _create_source_sync_path(
    source_data: SourceCreate,
    content_state: dict[str, Any],
    transformation_ids: List[str],
) -> SourceResponse:
    """SYNC PATH: Execute synchronously using execute_command_sync."""
    logger.info("Using sync processing path")

    try:
        # Import command modules to ensure they're registered
        import commands.source_commands  # noqa: F401

        # Create source record - let SurrealDB generate the ID
        source = Source(
            title=source_data.title or "Processing...",
            topics=[],
        )
        await source.save()

        # Add source to notebooks immediately so it appears in the UI
        # The source_graph will skip adding duplicates
        for notebook_id in source_data.notebooks or []:
            await source.add_to_notebook(notebook_id)

        # Execute command synchronously
        command_input = SourceProcessingInput(
            source_id=str(source.id),
            content_state=content_state,
            notebook_ids=source_data.notebooks,
            transformations=transformation_ids,
            embed=source_data.embed,
        )

        # Run in thread pool to avoid blocking the event loop
        # execute_command_sync uses asyncio.run() internally which can't
        # be called from an already-running event loop (FastAPI)
        result = await asyncio.to_thread(
            execute_command_sync,
            "open_notebook",  # app name
            "process_source",  # command name
            command_input.model_dump(),
            timeout=300,  # 5 minute timeout for sync processing
        )

        if not result.is_success():
            logger.error(f"Sync processing failed: {result.error_message}")
            # Clean up source record
            try:
                await source.delete()
            except Exception:
                pass
            raise HTTPException(
                status_code=500,
                detail=f"Processing failed: {_truncate_error(result.error_message)}",
            )

        # Get the processed source
        if not source.id:
            raise HTTPException(status_code=500, detail="Source ID is missing")
        processed_source = await Source.get(source.id)
        if not processed_source:
            raise HTTPException(status_code=500, detail="Processed source not found")

        embedded_chunks = await processed_source.get_embedded_chunks()
        # No command_id or status for sync processing (legacy behavior)
        return _source_to_response(processed_source, embedded_chunks=embedded_chunks)

    except Exception as e:
        logger.error(f"Sync processing failed: {e}")
        # The uploaded file (if any) is cleaned up by create_source's handlers
        raise


@router.post("/sources", response_model=SourceResponse)
async def create_source(
    form_data: tuple[SourceCreate, Optional[UploadFile]] = Depends(
        parse_source_form_data
    ),
):
    """Create a new source with support for both JSON and multipart form data."""
    source_data, upload_file = form_data

    # Initialize file_path before try block so exception handlers can reference it
    file_path = None
    upload_hash: Optional[str] = None

    try:
        # Verify all specified notebooks exist (backward compatibility support)
        for notebook_id in source_data.notebooks or []:
            notebook = await Notebook.get(notebook_id)
            if not notebook:
                raise HTTPException(
                    status_code=404, detail=f"Notebook {notebook_id} not found"
                )

        # Handle file upload if provided
        if upload_file and source_data.type == "upload":
            try:
                file_path, upload_hash = await save_uploaded_file(upload_file)
            except Exception as e:
                logger.error(f"File upload failed: {e}")
                raise HTTPException(status_code=400, detail="File upload failed")

        # Prepare content_state for processing (type validation + SSRF/LFI guards)
        content_state = await _build_content_state(source_data, file_path)

        # Duplicate pre-check (text/upload only - their content is known
        # before the async pipeline runs; link sources are checked post-hoc,
        # see open_notebook/graphs/source.py save_source()).
        await _check_duplicate_before_create(source_data, upload_hash)

        # Validate transformations exist
        transformation_ids = source_data.transformations or []
        for trans_id in transformation_ids:
            transformation = await Transformation.get(trans_id)
            if not transformation:
                raise HTTPException(
                    status_code=404, detail=f"Transformation {trans_id} not found"
                )

        # Branch based on processing mode
        if source_data.async_processing:
            return await _create_source_async_path(
                source_data, content_state, transformation_ids, file_path
            )
        return await _create_source_sync_path(
            source_data, content_state, transformation_ids
        )

    except HTTPException:
        # Clean up uploaded file on HTTP exceptions if we created it
        _cleanup_uploaded_file(file_path, upload_file)
        raise
    except InvalidInputError as e:
        # Clean up uploaded file on validation errors if we created it
        _cleanup_uploaded_file(file_path, upload_file)
        raise HTTPException(status_code=400, detail=str(e))
    except OpenNotebookError:
        # Clean up uploaded file before the global handlers map the error
        _cleanup_uploaded_file(file_path, upload_file)
        raise
    except Exception as e:
        logger.error(f"Error creating source: {str(e)}")
        # Clean up uploaded file on unexpected errors if we created it
        _cleanup_uploaded_file(file_path, upload_file)
        raise HTTPException(status_code=500, detail="Error creating source")


@router.post("/sources/json", response_model=SourceResponse)
async def create_source_json(source_data: SourceCreate):
    """Create a new source using JSON payload (legacy endpoint for backward compatibility)."""
    # Convert to form data format and call main endpoint
    form_data = (source_data, None)
    return await create_source(form_data)


async def _resolve_source_file(source_id: str) -> tuple[str, str]:
    source = await Source.get(source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")

    file_path = source.asset.file_path if source.asset else None
    if not file_path:
        raise HTTPException(status_code=404, detail="Source has no file to download")

    safe_root = os.path.realpath(UPLOADS_FOLDER)
    resolved_path = os.path.realpath(file_path)

    if resolved_path != safe_root and not resolved_path.startswith(safe_root + os.sep):
        logger.warning(
            f"Blocked download outside uploads directory for source {source_id}: {resolved_path}"
        )
        raise HTTPException(status_code=403, detail="Access to file denied")

    if not os.path.exists(resolved_path):
        raise HTTPException(status_code=404, detail="File not found on server")

    filename = os.path.basename(resolved_path)
    return resolved_path, filename


def _is_source_file_available(source: Source) -> Optional[bool]:
    if not source or not source.asset or not source.asset.file_path:
        return None

    file_path = source.asset.file_path
    safe_root = os.path.realpath(UPLOADS_FOLDER)
    resolved_path = os.path.realpath(file_path)

    if resolved_path != safe_root and not resolved_path.startswith(safe_root + os.sep):
        return False

    return os.path.exists(resolved_path)


@router.get("/sources/{source_id}", response_model=SourceResponse)
async def get_source(source_id: str):
    """Get a specific source by ID."""
    try:
        source = await Source.get(source_id)
        if not source:
            raise HTTPException(status_code=404, detail="Source not found")

        await _stamp_source_view(source.id or source_id)

        # Get status information if command exists
        status = None
        processing_info = None
        if source.command:
            try:
                status = await source.get_status()
                processing_info = await source.get_processing_progress()
            except Exception as e:
                logger.warning(f"Failed to get status for source {source_id}: {e}")
                status = "unknown"

        embedded_chunks = await source.get_embedded_chunks()

        # Get associated notebooks
        notebooks_query = await repo_query(
            "SELECT VALUE out FROM reference WHERE in = $source_id",
            {"source_id": ensure_record_id(source.id or source_id)},
        )
        notebook_ids = (
            [str(nb_id) for nb_id in notebooks_query] if notebooks_query else []
        )

        # Resolve the duplicate's title for display (list view skips this -
        # it would be an extra query per row - detail view is a single fetch).
        duplicate_of_title = None
        if source.duplicate_of:
            try:
                duplicate_source = await Source.get(str(source.duplicate_of))
                duplicate_of_title = duplicate_source.title if duplicate_source else None
            except Exception as e:
                logger.warning(
                    f"Failed to resolve duplicate_of title for source {source_id}: {e}"
                )

        return _source_to_response(
            source,
            embedded_chunks=embedded_chunks,
            file_available=_is_source_file_available(source),
            # Status fields
            command_id=str(source.command) if source.command else None,
            status=status,
            processing_info=processing_info,
            # Notebook associations
            notebooks=notebook_ids,
            duplicate_of_title=duplicate_of_title,
        )
    except HTTPException:
        raise
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Source not found")
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error fetching source {source_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Error fetching source")


@router.head("/sources/{source_id}/download")
async def check_source_file(source_id: str):
    """Check if a source has a downloadable file."""
    try:
        await _resolve_source_file(source_id)
        return Response(status_code=200)
    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error checking file for source {source_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to verify file")


@router.get("/sources/{source_id}/download")
async def download_source_file(source_id: str):
    """Download the original file associated with an uploaded source."""
    try:
        resolved_path, filename = await _resolve_source_file(source_id)
        return FileResponse(
            path=resolved_path,
            filename=filename,
            media_type="application/octet-stream",
        )
    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error downloading file for source {source_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to download source file")


@router.get("/sources/{source_id}/status", response_model=SourceStatusResponse)
async def get_source_status(source_id: str):
    """Get processing status for a source."""
    try:
        # First, verify source exists
        source = await Source.get(source_id)
        if not source:
            raise HTTPException(status_code=404, detail="Source not found")

        # Check if this is a legacy source (no command)
        if not source.command:
            return SourceStatusResponse(
                status=None,
                message="Legacy source (completed before async processing)",
                processing_info=None,
                command_id=None,
            )

        # Get command status and processing info
        try:
            status = await source.get_status()
            processing_info = await source.get_processing_progress()

            # Normalize again defensively: get_status() is mockable/overridable
            # and a raw CommandStatus would render as "CommandStatus.NEW".
            status = normalize_job_status(status)
            message = SOURCE_STATUS_MESSAGES[status]

            return SourceStatusResponse(
                status=status,
                message=message,
                processing_info=processing_info,
                command_id=str(source.command) if source.command else None,
            )

        except Exception as e:
            logger.warning(f"Failed to get status for source {source_id}: {e}")
            return SourceStatusResponse(
                status="unknown",
                message="Failed to retrieve processing status",
                processing_info=None,
                command_id=str(source.command) if source.command else None,
            )

    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error fetching status for source {source_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Error fetching source status")


@router.put("/sources/{source_id}", response_model=SourceResponse)
async def update_source(source_id: str, source_update: SourceUpdate):
    """Update a source."""
    try:
        source = await Source.get(source_id)
        if not source:
            raise HTTPException(status_code=404, detail="Source not found")

        # Update only provided fields
        if source_update.title is not None:
            source.title = source_update.title
        if source_update.topics is not None:
            source.topics = source_update.topics

        await source.save()

        embedded_chunks = await source.get_embedded_chunks()
        return _source_to_response(source, embedded_chunks=embedded_chunks)
    except HTTPException:
        raise
    except InvalidInputError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error updating source {source_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Error updating source")


async def _set_archived(source_id: str, archived: bool) -> SourceResponse:
    source = await Source.get(source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")

    source.archived = archived
    await source.save()

    embedded_chunks = await source.get_embedded_chunks()
    return _source_to_response(source, embedded_chunks=embedded_chunks)


@router.post("/sources/{source_id}/archive", response_model=SourceResponse)
async def archive_source(source_id: str):
    """Archive a source: hides it from the default list view without
    deleting it - its chunks/embeddings/insights/notebook links stay intact.
    Mirrors Notebook archive/unarchive (api/routers/notebooks.py)."""
    try:
        return await _set_archived(source_id, True)
    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error archiving source {source_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Error archiving source")


@router.post("/sources/{source_id}/unarchive", response_model=SourceResponse)
async def unarchive_source(source_id: str):
    """Restore an archived source to the default list view."""
    try:
        return await _set_archived(source_id, False)
    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error unarchiving source {source_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Error unarchiving source")


@router.get("/sources/{source_id}/delete-preview", response_model=SourceDeletePreview)
async def get_source_delete_preview(source_id: str):
    """Explicit delete-impact summary, so the confirmation dialog can name
    exactly what will be removed instead of a generic "are you sure?"."""
    try:
        source = await Source.get(source_id)
        if not source:
            raise HTTPException(status_code=404, detail="Source not found")

        preview = await source.get_delete_preview()
        return SourceDeletePreview(source_id=source_id, **preview)
    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error getting delete preview for source {source_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Error getting delete preview")


@router.post("/sources/{source_id}/retry", response_model=SourceResponse)
async def retry_source_processing(source_id: str):
    """Retry processing for a failed, canceled or stuck source.

    Idempotent: if the source's current job is still queued or processing
    (e.g. a double click), no new job is enqueued and the existing job is
    returned unchanged.
    """
    async with _retry_lock(source_id):
        return await _retry_source_processing_locked(source_id)


async def _retry_source_processing_locked(source_id: str):
    try:
        # First, verify source exists
        source = await Source.get(source_id)
        if not source:
            raise HTTPException(status_code=404, detail="Source not found")

        # If a job for this source is already active, return it instead of
        # enqueuing a duplicate.
        if source.command:
            try:
                current_status = normalize_job_status(await source.get_status())
            except Exception as e:
                logger.warning(
                    f"Failed to check current status for source {source_id}: {e}"
                )
                current_status = "unknown"  # Continue with retry
            if current_status in ACTIVE_JOB_STATUSES:
                logger.info(
                    f"Retry for source {source_id} ignored: job {source.command} "
                    f"is already {current_status}"
                )
                return _source_to_response(
                    source,
                    embedded_chunks=await source.get_embedded_chunks(),
                    command_id=str(source.command),
                    status=current_status,
                    processing_info={"retry": False, "already_active": True},
                )

        # Get notebooks that this source belongs to. `reference` is a graph edge
        # (RELATE source->reference->notebook), so it only has `in`/`out` columns —
        # there is no `source`/`notebook` column. Mirror the working query at the
        # source-list path above. See issue #861.
        references = await repo_query(
            "SELECT VALUE out FROM reference WHERE in = $source_id",
            {"source_id": ensure_record_id(source.id or source_id)},
        )
        notebook_ids = [str(nb_id) for nb_id in references] if references else []

        if not notebook_ids:
            raise HTTPException(
                status_code=400, detail="Source is not associated with any notebooks"
            )

        # Prepare content_state based on source asset
        content_state = {}
        if source.asset:
            if source.asset.file_path:
                # Don't re-queue a retry for a file content-core can't extract.
                await _assert_file_supported(source.asset.file_path)
                content_state = {
                    "file_path": source.asset.file_path,
                    "delete_source": False,  # Don't delete on retry
                }
            elif source.asset.url:
                content_state = {"url": source.asset.url}
            else:
                raise HTTPException(
                    status_code=400, detail="Source asset has no file_path or url"
                )
        else:
            # Check if it's a text source by trying to get full_text
            if source.full_text:
                content_state = {"content": source.full_text}
            else:
                raise HTTPException(
                    status_code=400, detail="Cannot determine source content for retry"
                )

        try:
            # Import command modules to ensure they're registered
            import commands.source_commands  # noqa: F401

            # Submit new command for background processing
            command_input = SourceProcessingInput(
                source_id=str(source.id),
                content_state=content_state,
                notebook_ids=notebook_ids,
                transformations=[],  # Use default transformations on retry
                embed=True,  # Always embed on retry
            )

            command_id = await CommandService.submit_command_job(
                "open_notebook",  # app name
                "process_source",  # command name
                command_input.model_dump(),
            )

            logger.info(
                f"Submitted retry processing command: {command_id} for source {source_id}"
            )

            # Update source with new command ID
            # command_id already includes 'command:' prefix
            source.command = ensure_record_id(command_id)
            await source.save()

            # Get current embedded chunks count
            embedded_chunks = await source.get_embedded_chunks()

            # Return updated source response
            return _source_to_response(
                source,
                embedded_chunks=embedded_chunks,
                command_id=command_id,
                status="queued",
                processing_info={"retry": True, "queued": True},
            )

        except Exception as e:
            logger.error(
                f"Failed to submit retry processing command for source {source_id}: {e}"
            )
            raise HTTPException(
                status_code=500, detail="Failed to queue retry processing"
            )

    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error retrying source processing for {source_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Error retrying source processing")


def parse_replace_form_data(
    type: str = Form(...),
    url: Optional[str] = Form(None),
    content: Optional[str] = Form(None),
    delete_source: str = Form("false"),
    file: Optional[UploadFile] = File(None),
) -> tuple[str, Optional[str], Optional[str], bool, Optional[UploadFile]]:
    """Parse the multipart body for POST /sources/{id}/replace.

    Deliberately a small subset of parse_source_form_data's fields - a
    replace keeps the source's existing notebooks/transformations/embed
    preference; only the content itself changes."""

    def str_to_bool(value: str) -> bool:
        return value.lower() in ("true", "1", "yes", "on")

    if type not in ("link", "upload", "text"):
        raise HTTPException(
            status_code=400,
            detail="Invalid source type. Must be link, upload, or text",
        )
    return type, url, content, str_to_bool(delete_source), file


@router.post("/sources/{source_id}/replace", response_model=SourceResponse)
async def replace_source_content(
    source_id: str,
    form_data: tuple[str, Optional[str], Optional[str], bool, Optional[UploadFile]] = (
        Depends(parse_replace_form_data)
    ),
):
    """Replace a source's content in place: same source_id, same chat/
    notebook links, re-run through the ingestion pipeline. Reuses the same
    no-duplicate-chunks logic as retry (embed_source_command deletes the
    old source_embedding rows before inserting the new ones - see
    commands/embedding_commands.py) and additionally clears the old
    source_insight rows, since insights generated from the *previous*
    content would otherwise look like they describe the new content.
    """
    type_, url, content, delete_source_flag, upload_file = form_data
    file_path: Optional[str] = None

    try:
        source = await Source.get(source_id)
        if not source:
            raise HTTPException(status_code=404, detail="Source not found")

        if upload_file and type_ == "upload":
            try:
                file_path, _ = await save_uploaded_file(upload_file)
            except Exception as e:
                logger.error(f"File upload failed: {e}")
                raise HTTPException(status_code=400, detail="File upload failed")

        replace_request = SourceCreate(
            type=type_,
            url=url,
            content=content,
            file_path=file_path,
            delete_source=delete_source_flag,
        )
        content_state = await _build_content_state(replace_request, file_path)

        notebook_ids = await _get_source_notebook_ids(source_id)

        # Clear insights generated from the content being replaced. Their
        # embeddings (source_insight has no separate embedding table entry
        # beyond its own `embedding` field) go with them via the delete.
        try:
            await repo_query(
                "DELETE source_insight WHERE source = $source_id",
                {"source_id": ensure_record_id(source_id)},
            )
        except Exception as e:
            logger.warning(
                f"Failed to clear stale insights before replacing source {source_id}: {e}"
            )

        import commands.source_commands  # noqa: F401

        command_input = SourceProcessingInput(
            source_id=str(source.id),
            content_state=content_state,
            notebook_ids=notebook_ids,
            transformations=[],
            embed=True,
        )
        command_id = await CommandService.submit_command_job(
            "open_notebook",
            "process_source",
            command_input.model_dump(),
        )
        logger.info(
            f"Submitted replace-content command: {command_id} for source {source_id}"
        )

        source.command = ensure_record_id(command_id)
        await source.save()

        embedded_chunks = await source.get_embedded_chunks()
        return _source_to_response(
            source,
            embedded_chunks=embedded_chunks,
            command_id=command_id,
            status="queued",
            processing_info={"replace": True, "queued": True},
        )

    except HTTPException:
        _cleanup_uploaded_file(file_path, upload_file)
        raise
    except OpenNotebookError:
        _cleanup_uploaded_file(file_path, upload_file)
        raise
    except Exception as e:
        logger.error(f"Error replacing content for source {source_id}: {str(e)}")
        _cleanup_uploaded_file(file_path, upload_file)
        raise HTTPException(status_code=500, detail="Error replacing source content")


@router.delete("/sources/{source_id}")
async def delete_source(source_id: str):
    """Delete a source."""
    try:
        source = await Source.get(source_id)
        if not source:
            raise HTTPException(status_code=404, detail="Source not found")

        await source.delete()

        return {"message": "Source deleted successfully"}
    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error deleting source {source_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Error deleting source")


@router.get("/sources/{source_id}/insights", response_model=List[SourceInsightResponse])
async def get_source_insights(source_id: str):
    """Get all insights for a specific source."""
    try:
        source = await Source.get(source_id)
        if not source:
            raise HTTPException(status_code=404, detail="Source not found")

        insights = await source.get_insights()
        return [
            SourceInsightResponse(
                id=insight.id or "",
                source_id=source_id,
                insight_type=insight.insight_type,
                content=insight.content,
                created=insight.created.isoformat() if insight.created else None,
                updated=insight.updated.isoformat() if insight.updated else None,
            )
            for insight in insights
        ]
    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error fetching insights for source {source_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Error fetching insights")


@router.post(
    "/sources/{source_id}/insights",
    response_model=InsightCreationResponse,
    status_code=202,
)
async def create_source_insight(source_id: str, request: CreateSourceInsightRequest):
    """
    Start insight generation for a source by running a transformation.

    This endpoint returns immediately with a 202 Accepted status.
    The transformation runs asynchronously in the background via the job queue.
    Poll GET /sources/{source_id}/insights to see when the insight is ready.
    """
    try:
        # Validate source exists
        source = await Source.get(source_id)
        if not source:
            raise HTTPException(status_code=404, detail="Source not found")

        # Validate transformation exists
        transformation = await Transformation.get(request.transformation_id)
        if not transformation:
            raise HTTPException(status_code=404, detail="Transformation not found")

        # Submit transformation as background job (fire-and-forget)
        command_id = submit_command(
            "open_notebook",
            "run_transformation",
            {
                "source_id": source_id,
                "transformation_id": request.transformation_id,
            },
        )
        logger.info(
            f"Submitted run_transformation command {command_id} for source {source_id}"
        )

        # Return immediately with command_id for status tracking
        return InsightCreationResponse(
            status="pending",
            message="Insight generation started",
            source_id=source_id,
            transformation_id=request.transformation_id,
            command_id=str(command_id),
        )

    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error starting insight generation for source {source_id}: {e}")
        raise HTTPException(status_code=500, detail="Error starting insight generation")
