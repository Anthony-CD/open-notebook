"""
Google Drive router.

Thin routes only - all OAuth/Drive HTTP calls and token-refresh logic live in
api/drive_service.py, per this codebase's API layer convention
(open_notebook/AGENTS.md: "routers stay thin, business logic goes in
*_service.py"). Domain errors are raised as the typed exceptions from
open_notebook.exceptions; the global handlers registered in api/main.py map
them to the right HTTP status (AuthenticationError->401,
ConfigurationError->422, ExternalServiceError->502, NotFoundError->404).
"""

from typing import Optional

from fastapi import APIRouter, Query
from fastapi.responses import RedirectResponse
from loguru import logger

from api import drive_service
from api.models import (
    DriveAuthUrlResponse,
    DriveDisconnectResponse,
    DriveFileListResponse,
    DriveFileResponse,
    DriveImportRequest,
    DriveStatusResponse,
    SourceResponse,
)
from open_notebook.exceptions import OpenNotebookError

router = APIRouter()


@router.get("/drive/auth-url", response_model=DriveAuthUrlResponse)
async def get_auth_url():
    """Build the Google OAuth consent URL. The frontend does a full-page
    redirect to this URL (not an API call it awaits) - standard OAuth flow."""
    return DriveAuthUrlResponse(auth_url=drive_service.build_auth_url())


@router.get("/drive/callback")
async def oauth_callback(
    code: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
):
    """OAuth redirect target: Google sends the browser here with `code`, or
    with `error` (e.g. `access_denied` when the user clicks Cancel) and NO
    code - so `code` must be optional or a cancelled consent would 422.

    This always redirects the browser back to the frontend Settings page
    rather than returning JSON, since a browser navigation landing here
    should never show a raw API error. Outcome is signalled via
    `drive=connected|cancelled|error` (+ `message` for errors).
    """
    if error:
        if error == "access_denied":
            return RedirectResponse(
                url=drive_service.frontend_settings_url({"drive": "cancelled"})
            )
        logger.warning(f"Google Drive OAuth callback returned error={error}")
        return RedirectResponse(
            url=drive_service.frontend_settings_url(
                {"drive": "error", "message": f"Google: {error}"}
            )
        )
    if not code:
        return RedirectResponse(
            url=drive_service.frontend_settings_url(
                {"drive": "error", "message": "Missing authorization code"}
            )
        )
    try:
        await drive_service.handle_oauth_callback(code)
    except OpenNotebookError as e:
        logger.warning(f"Google Drive OAuth callback failed: {e}")
        return RedirectResponse(
            url=drive_service.frontend_settings_url(
                {"drive": "error", "message": str(e)}
            )
        )
    except Exception as e:
        # e.g. DB write failure - still send the browser back with a clear
        # error instead of a raw 500 page and a half-finished connection.
        logger.exception(f"Unexpected error in Google Drive OAuth callback: {e}")
        return RedirectResponse(
            url=drive_service.frontend_settings_url(
                {"drive": "error", "message": "Unexpected error while saving the connection"}
            )
        )
    return RedirectResponse(url=drive_service.frontend_settings_url({"drive": "connected"}))


@router.get("/drive/status", response_model=DriveStatusResponse)
async def get_status():
    status = await drive_service.get_status()
    return DriveStatusResponse(**status)


@router.delete("/drive/disconnect", response_model=DriveDisconnectResponse)
async def disconnect():
    await drive_service.disconnect()
    return DriveDisconnectResponse(message="Google Drive disconnected")


@router.get("/drive/files", response_model=DriveFileListResponse)
async def list_files(
    query: str = Query(None, description="Optional file name search"),
    page_token: str = Query(None, description="Pagination token from a previous response"),
):
    result = await drive_service.list_files(query, page_token)
    return DriveFileListResponse(
        files=[
            DriveFileResponse(
                id=f["id"],
                name=f.get("name", ""),
                mime_type=f.get("mimeType", ""),
                modified_time=f.get("modifiedTime"),
                icon_link=f.get("iconLink"),
            )
            for f in result["files"]
        ],
        next_page_token=result.get("next_page_token"),
    )


@router.post("/drive/import", response_model=SourceResponse)
async def import_file(request: DriveImportRequest):
    return await drive_service.import_file(request.file_id, request.notebook_id)
