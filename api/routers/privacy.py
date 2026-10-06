"""
Privacy center router (P2 "Seguridad").

Thin HTTP layer over api.privacy_service - see that module's docstring for
the exact scope of export/delete. Business logic stays there per
open_notebook/AGENTS.md's "routes -> services -> models".
"""

from fastapi import APIRouter, HTTPException
from loguru import logger

from api.models import (
    PrivacyDeleteRequest,
    PrivacyDeleteResponse,
    PrivacyExportResponse,
)
from api.privacy_service import build_export, delete_all_data
from open_notebook.exceptions import OpenNotebookError

router = APIRouter(prefix="/privacy", tags=["privacy"])


@router.get("/export", response_model=PrivacyExportResponse)
async def export_data():
    """Export this instance's content as a JSON bundle. See
    api/privacy_service.py for exact scope (metadata + text, not binary
    file/audio blobs)."""
    try:
        bundle = await build_export()
        return PrivacyExportResponse(**bundle)
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error building privacy export: {e}")
        raise HTTPException(status_code=500, detail="Failed to build data export")


@router.delete("/data", response_model=PrivacyDeleteResponse)
async def delete_data(request: PrivacyDeleteRequest):
    """Permanently delete all notebooks/sources/notes/chats/study sets/
    podcast episodes on this instance (full wipe - see
    api/privacy_service.py for exactly what is and isn't included).

    Requires `confirm` to exactly equal the phrase the frontend's
    destructive-confirmation dialog shows the user - a second,
    server-enforced check on top of the UI dialog, not a replacement for
    it.
    """
    try:
        deleted = await delete_all_data(request.confirm)
        return PrivacyDeleteResponse(
            message="All data has been permanently deleted.", deleted=deleted
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error deleting all data: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete data")
