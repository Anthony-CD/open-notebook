"""
Google Drive Service

Business logic for the Google Drive integration: OAuth connect/disconnect,
browsing files (metadata only), and importing a file as a Source by handing
its bytes to the existing source-creation pipeline (api/routers/sources.py) -
so a Drive-imported source gets the same async content-core extraction,
embedding eligibility, and notebook association as an uploaded file.

Routers stay thin (api/routers/drive.py); all Drive/Google HTTP calls and the
token-refresh logic live here, per this codebase's API layer convention
(open_notebook/AGENTS.md).

The database record (open_notebook:drive_connection) is the single source of
truth for the connection state: every entry point re-reads it via
DriveConnection.load_fresh() instead of trusting the per-process RecordModel
singleton cache.

Env vars (read at call time, matching how other optional integrations in
this codebase resolve config - e.g. CORS_ORIGINS in api/main.py):
    GOOGLE_DRIVE_CLIENT_ID
    GOOGLE_DRIVE_CLIENT_SECRET
    GOOGLE_DRIVE_REDIRECT_URI
    GOOGLE_DRIVE_FRONTEND_URL  (optional; base URL the OAuth callback sends
                                the browser back to - see frontend_settings_url)
"""

import asyncio
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlencode

import httpx
from loguru import logger

from api.models import SourceCreate, SourceResponse
from open_notebook.domain.drive_connection import DriveConnection, as_utc_datetime
from open_notebook.domain.notebook import Notebook
from open_notebook.exceptions import (
    AuthenticationError,
    ConfigurationError,
    ExternalServiceError,
    NotFoundError,
)

GOOGLE_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
GOOGLE_REVOKE_ENDPOINT = "https://oauth2.googleapis.com/revoke"
GOOGLE_USERINFO_ENDPOINT = "https://www.googleapis.com/oauth2/v2/userinfo"
DRIVE_FILES_ENDPOINT = "https://www.googleapis.com/drive/v3/files"

DRIVE_READONLY_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
DRIVE_SCOPES = f"{DRIVE_READONLY_SCOPE} https://www.googleapis.com/auth/userinfo.email"

# Reasons surfaced to the UI via GET /drive/status `reconnect_reason`.
REASON_REVOKED = "revoked"
REASON_MISSING_SCOPE = "missing_scope"

DRIVE_FILE_LIST_FIELDS = "nextPageToken,files(id,name,mimeType,modifiedTime,iconLink)"

# Native Google Docs/Sheets/Slides have no binary representation - export them
# through Drive's files.export endpoint into a format content-core already
# knows how to extract (see open_notebook/graphs/source.py content_process).
# Other google-apps.* types (forms, drawings, sites, apps script, ...) aren't
# meaningfully exportable as document text and are rejected up front.
GOOGLE_NATIVE_EXPORT_MAP: Dict[str, Tuple[str, str]] = {
    "application/vnd.google-apps.document": ("text/plain", ".txt"),
    "application/vnd.google-apps.spreadsheet": ("text/csv", ".csv"),
    "application/vnd.google-apps.presentation": ("application/pdf", ".pdf"),
}
GOOGLE_NATIVE_MIME_PREFIX = "application/vnd.google-apps."

HTTP_TIMEOUT_SECONDS = 30.0


class DriveReconnectRequired(ConfigurationError):
    """The Drive connection is missing or unusable (never connected, refresh
    token revoked, Drive scope not granted). Subclasses ConfigurationError so
    the global handler maps it to 422 - deliberately NOT AuthenticationError,
    whose 401 makes the frontend's axios interceptor log the user out of the
    whole app just because a Google grant was revoked."""


class _InvalidGrant(AuthenticationError):
    """Google's token endpoint answered `invalid_grant` - the refresh token
    (or authorization code) is revoked/expired and can never work again."""


def _client_id() -> str:
    return os.getenv("GOOGLE_DRIVE_CLIENT_ID", "")


def _client_secret() -> str:
    return os.getenv("GOOGLE_DRIVE_CLIENT_SECRET", "")


def _redirect_uri() -> str:
    return os.getenv("GOOGLE_DRIVE_REDIRECT_URI", "")


def is_configured() -> bool:
    return bool(_client_id() and _client_secret() and _redirect_uri())


def frontend_settings_url(query: Dict[str, str]) -> str:
    """Where the OAuth callback sends the browser back to.

    A relative `/settings` only works when Google redirects through the
    frontend's /api proxy (GOOGLE_DRIVE_REDIRECT_URI on the frontend origin).
    When the redirect URI points straight at the API port (e.g.
    http://localhost:5055/api/drive/callback) a relative redirect lands on the
    API, which has no /settings page - so GOOGLE_DRIVE_FRONTEND_URL (e.g.
    http://localhost:3000) can be set to redirect there explicitly.
    """
    base = os.getenv("GOOGLE_DRIVE_FRONTEND_URL", "").strip().rstrip("/")
    return f"{base}/settings?{urlencode(query)}"


def _require_oauth_config() -> None:
    if not is_configured():
        raise ConfigurationError(
            "Google Drive is not configured. Set GOOGLE_DRIVE_CLIENT_ID, "
            "GOOGLE_DRIVE_CLIENT_SECRET and GOOGLE_DRIVE_REDIRECT_URI to enable it."
        )


def build_auth_url() -> str:
    """Build the Google OAuth consent screen URL.

    access_type=offline + prompt=consent guarantee a refresh_token comes back
    even on a repeat connect (Google only issues one on the very first
    consent for a given user+client otherwise).
    """
    _require_oauth_config()
    params = {
        "client_id": _client_id(),
        "redirect_uri": _redirect_uri(),
        "response_type": "code",
        "scope": DRIVE_SCOPES,
        "access_type": "offline",
        "prompt": "consent",
    }
    return f"{GOOGLE_AUTH_ENDPOINT}?{urlencode(params)}"


async def _post_token_request(data: Dict[str, str]) -> Dict[str, Any]:
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                GOOGLE_TOKEN_ENDPOINT, data=data, timeout=HTTP_TIMEOUT_SECONDS
            )
    except httpx.HTTPError as e:
        logger.error(f"Network error calling Google token endpoint: {e}")
        raise ExternalServiceError(f"Failed to reach Google's token endpoint: {e}") from e

    if response.status_code >= 400:
        error_code = ""
        try:
            body = response.json()
            if isinstance(body, dict):
                error_code = str(body.get("error", ""))
        except ValueError:
            pass
        # Log only status + Google's error code, never the raw body.
        logger.warning(
            f"Google token endpoint returned {response.status_code} "
            f"({error_code or 'no error code'})"
        )
        if error_code == "invalid_grant":
            raise _InvalidGrant(
                "Google rejected the stored authorization (it was revoked or "
                "expired). Reconnect Google Drive in Settings."
            )
        if response.status_code >= 500:
            raise ExternalServiceError(
                "Google's token endpoint is unavailable. Try again later."
            )
        raise AuthenticationError(
            "Google rejected the token request. The authorization code may "
            "have expired, or the connection needs to be re-established."
        )
    try:
        return response.json()
    except ValueError as e:
        raise ExternalServiceError(
            "Google's token endpoint returned an invalid response"
        ) from e


async def _exchange_code_for_tokens(code: str) -> Dict[str, Any]:
    _require_oauth_config()
    return await _post_token_request(
        {
            "code": code,
            "client_id": _client_id(),
            "client_secret": _client_secret(),
            "redirect_uri": _redirect_uri(),
            "grant_type": "authorization_code",
        }
    )


async def _refresh_access_token(refresh_token: str) -> Dict[str, Any]:
    _require_oauth_config()
    return await _post_token_request(
        {
            "refresh_token": refresh_token,
            "client_id": _client_id(),
            "client_secret": _client_secret(),
            "grant_type": "refresh_token",
        }
    )


async def _fetch_account_email(access_token: str) -> Optional[str]:
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                GOOGLE_USERINFO_ENDPOINT,
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=HTTP_TIMEOUT_SECONDS,
            )
        if response.status_code >= 400:
            logger.warning(f"Google userinfo endpoint returned {response.status_code}")
            return None
        return response.json().get("email")
    except (httpx.HTTPError, ValueError) as e:
        logger.warning(f"Failed to fetch Drive account email: {e}")
        return None


def _expiry_from_expires_in(expires_in: Any) -> datetime:
    try:
        seconds = int(expires_in)
    except (TypeError, ValueError):
        seconds = 3600  # Google's default access token lifetime
    return datetime.now(timezone.utc) + timedelta(seconds=seconds)


async def handle_oauth_callback(code: str) -> DriveConnection:
    """Exchange the authorization code for tokens, fetch the account email,
    and persist the connection. Called by GET /api/drive/callback.

    Every failure raises (nothing is swallowed between token exchange and
    persistence) so the router redirects with drive=error instead of
    claiming success.
    """
    tokens = await _exchange_code_for_tokens(code)
    access_token = tokens.get("access_token")
    refresh_token = tokens.get("refresh_token")
    if not access_token:
        raise AuthenticationError("Google did not return an access token")
    if not refresh_token:
        # Should not happen with access_type=offline&prompt=consent, but fail
        # loudly rather than silently storing a connection that can never
        # refresh once the short-lived access token expires.
        raise AuthenticationError(
            "Google did not return a refresh token. Disconnect any existing "
            "Open Notebook access at https://myaccount.google.com/permissions "
            "and try connecting again."
        )

    granted_scopes = str(tokens.get("scope") or "").strip()
    account_email = await _fetch_account_email(access_token)

    conn = await DriveConnection.load_fresh()
    await conn.store_tokens(
        access_token=access_token,
        refresh_token=refresh_token,
        token_expiry=_expiry_from_expires_in(tokens.get("expires_in")),
        account_email=account_email,
        granted_scopes=granted_scopes or None,
        new_connection=True,
    )

    # Google's granular consent lets the user untick the Drive checkbox; the
    # connection is then useless for browsing/importing. Keep the account on
    # the record but flag it, rather than show a fake "connected".
    if granted_scopes and DRIVE_READONLY_SCOPE not in granted_scopes.split():
        await conn.mark_needs_reconnect(REASON_MISSING_SCOPE)
        raise DriveReconnectRequired(
            "Google Drive access was not granted. Reconnect and allow access "
            "to your Drive files."
        )

    # Verify the write actually landed in the DB (the single source of truth)
    # before the router tells the browser "connected".
    persisted = await DriveConnection.load_fresh()
    if not persisted.is_connected:
        raise ExternalServiceError(
            "The Google Drive connection could not be saved. Try again."
        )
    return persisted


def _iso(value: Any) -> Optional[str]:
    dt = as_utc_datetime(value)
    return dt.isoformat() if dt else None


async def get_status() -> Dict[str, Any]:
    """Connection status read straight from the DB. Never includes tokens.
    Does not raise when OAuth isn't configured - the UI shows a dedicated
    "not configured" state from `configured=False`."""
    conn = await DriveConnection.load_fresh()
    needs_reconnect = bool(conn.needs_reconnect)
    return {
        "configured": is_configured(),
        "connected": conn.is_connected and not needs_reconnect,
        "account_email": conn.account_email,
        "granted_scopes": conn.scope_list,
        "connected_at": _iso(conn.connected_at),
        "last_sync_at": _iso(conn.last_sync_at),
        "needs_reconnect": needs_reconnect,
        "reconnect_reason": conn.reconnect_reason if needs_reconnect else None,
    }


async def _revoke_token(token: str) -> None:
    """Best-effort revoke at Google - a failure here must never block the
    user from clearing their local connection."""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                GOOGLE_REVOKE_ENDPOINT,
                data={"token": token},
                timeout=HTTP_TIMEOUT_SECONDS,
            )
        if response.status_code >= 400:
            # 400 invalid_token = already revoked/expired at Google; fine.
            logger.info(f"Google revoke returned {response.status_code} (continuing)")
    except httpx.HTTPError as e:
        logger.warning(f"Failed to revoke Google Drive token (continuing anyway): {e}")


async def disconnect() -> None:
    conn = await DriveConnection.load_fresh()
    token: Optional[str] = None
    try:
        token = conn.decrypt_refresh_token() or conn.decrypt_access_token()
    except Exception as e:  # undecryptable (key changed) - still clear locally
        logger.warning(f"Could not decrypt Drive token for revocation: {e}")
    if token:
        await _revoke_token(token)
    await conn.disconnect()


async def _get_connected_connection() -> DriveConnection:
    conn = await DriveConnection.load_fresh()
    if conn.needs_reconnect:
        raise DriveReconnectRequired(
            "Google Drive needs to be reconnected. Reconnect it in Settings."
        )
    if not conn.is_connected:
        raise DriveReconnectRequired(
            "Google Drive is not connected. Connect it in Settings first."
        )
    return conn


async def _refresh_and_store(conn: DriveConnection) -> str:
    refresh_token = conn.decrypt_refresh_token()
    if not refresh_token:
        await conn.mark_needs_reconnect(REASON_REVOKED)
        raise DriveReconnectRequired(
            "Google Drive connection has no refresh token. Reconnect it in Settings."
        )
    try:
        tokens = await _refresh_access_token(refresh_token)
    except _InvalidGrant as e:
        # Revoked at myaccount.google.com, password change, long inactivity,
        # "Testing" OAuth apps' 7-day refresh token expiry... - the grant is
        # dead; flag it instead of leaving a fake "connected".
        await conn.mark_needs_reconnect(REASON_REVOKED)
        raise DriveReconnectRequired(str(e)) from e
    access_token = tokens.get("access_token")
    if not access_token:
        raise ExternalServiceError(
            "Failed to refresh the Google Drive access token. Try again or "
            "reconnect it in Settings."
        )
    await conn.store_tokens(
        access_token=access_token,
        # Google's refresh grant normally does not return a new
        # refresh_token; store_tokens() preserves the existing one when
        # refresh_token is falsy here.
        refresh_token=tokens.get("refresh_token"),
        token_expiry=_expiry_from_expires_in(tokens.get("expires_in")),
        granted_scopes=tokens.get("scope"),
    )
    return access_token


async def _ensure_valid_access_token(
    conn: DriveConnection, force_refresh: bool = False
) -> str:
    """Shared refresh helper - proactively refreshes the access token when
    it's missing/expired/about to expire (or `force_refresh` after a Drive
    401), persists the refresh, and returns a usable plaintext token."""
    if force_refresh or conn.is_access_token_expired():
        return await _refresh_and_store(conn)
    token = conn.decrypt_access_token()
    if not token:
        return await _refresh_and_store(conn)
    return token


async def _drive_get(
    conn: DriveConnection, url: str, params: Dict[str, str], what: str
) -> httpx.Response:
    """GET a Drive API URL with the connection's token. On a 401 (access
    token invalidated before its expiry) refresh once and retry; a dead
    refresh grant is flagged needs_reconnect by _refresh_and_store."""
    access_token = await _ensure_valid_access_token(conn)
    for attempt in range(2):
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    url,
                    params=params,
                    headers={"Authorization": f"Bearer {access_token}"},
                    timeout=HTTP_TIMEOUT_SECONDS,
                )
        except httpx.HTTPError as e:
            logger.error(f"Network error during Drive {what}: {e}")
            raise ExternalServiceError(f"Failed to reach Google Drive: {e}") from e
        if response.status_code == 401 and attempt == 0:
            access_token = await _ensure_valid_access_token(conn, force_refresh=True)
            continue
        if response.status_code == 403 and "insufficient" in response.text.lower():
            # insufficientPermissions / ACCESS_TOKEN_SCOPE_INSUFFICIENT
            await conn.mark_needs_reconnect(REASON_MISSING_SCOPE)
            raise DriveReconnectRequired(
                "Google Drive access was not granted. Reconnect and allow "
                "access to your Drive files."
            )
        if response.status_code >= 400:
            logger.warning(f"Drive {what} returned {response.status_code}")
            raise ExternalServiceError(f"Google Drive rejected the {what} request")
        return response
    raise ExternalServiceError(f"Google Drive rejected the {what} request")


async def _mark_synced_best_effort(conn: DriveConnection) -> None:
    try:
        await conn.mark_synced()
    except Exception as e:  # bookkeeping only - never fail the user action
        logger.warning(f"Failed to record Drive last_sync_at: {e}")


async def list_files(query: Optional[str], page_token: Optional[str]) -> Dict[str, Any]:
    """List Drive files (metadata only - id, name, mimeType, modifiedTime,
    iconLink) matching an optional name search, per the "read metadata,
    extract text only when needed" requirement."""
    conn = await _get_connected_connection()

    params: Dict[str, str] = {
        "fields": DRIVE_FILE_LIST_FIELDS,
        "pageSize": "50",
        # Exclude trashed files and folders - only files can be imported as
        # sources. name contains is a simple, cheap server-side filter;
        # Drive's fullText search is intentionally not used here since we
        # only need metadata for browsing, not content search.
        "q": "trashed = false and mimeType != 'application/vnd.google-apps.folder'",
        "orderBy": "modifiedTime desc",
    }
    if query:
        # Escape backslashes and single quotes per Drive API query syntax.
        safe_query = query.replace("\\", "\\\\").replace("'", "\\'")
        params["q"] += f" and name contains '{safe_query}'"
    if page_token:
        params["pageToken"] = page_token

    response = await _drive_get(conn, DRIVE_FILES_ENDPOINT, params, "file list")
    try:
        data = response.json()
    except ValueError as e:
        raise ExternalServiceError("Google Drive returned an invalid file list") from e

    await _mark_synced_best_effort(conn)
    return {
        "files": data.get("files", []),
        "next_page_token": data.get("nextPageToken"),
    }


async def _download_file_bytes(
    conn: DriveConnection, file_id: str, mime_type: str
) -> Tuple[bytes, str]:
    """Download a Drive file's content, returning (bytes, extension).

    Native Google Docs/Sheets/Slides have no binary form and must go through
    files.export; anything else (PDF, docx, txt, ... already uploaded to
    Drive in a normal format) is fetched as-is via alt=media.
    """
    if mime_type.startswith(GOOGLE_NATIVE_MIME_PREFIX):
        export = GOOGLE_NATIVE_EXPORT_MAP.get(mime_type)
        if not export:
            raise ExternalServiceError(
                f"Google Drive file type '{mime_type}' cannot be exported as a document "
                "(only Docs, Sheets and Slides are supported)."
            )
        export_mime_type, extension = export
        url = f"{DRIVE_FILES_ENDPOINT}/{file_id}/export"
        params = {"mimeType": export_mime_type}
    else:
        url = f"{DRIVE_FILES_ENDPOINT}/{file_id}"
        params = {"alt": "media"}
        extension = ""

    response = await _drive_get(conn, url, params, "file download")
    return response.content, extension


async def _fetch_file_metadata(conn: DriveConnection, file_id: str) -> Dict[str, Any]:
    response = await _drive_get(
        conn,
        f"{DRIVE_FILES_ENDPOINT}/{file_id}",
        {"fields": "id,name,mimeType"},
        "file metadata",
    )
    try:
        return response.json()
    except ValueError as e:
        raise ExternalServiceError("Google Drive returned invalid file metadata") from e


def _drive_filename_with_extension(name: str, mime_type: str, export_extension: str) -> str:
    import mimetypes
    from pathlib import Path

    if export_extension:
        # Native Google Docs/Sheets/Slides: the Drive display name never
        # carries a real extension, so append the one for the export format.
        return f"{name}{export_extension}"

    if Path(name).suffix:
        return name

    guessed = mimetypes.guess_extension(mime_type) or ""
    return f"{name}{guessed}"


async def import_file(file_id: str, notebook_id: str) -> SourceResponse:
    """Download a Drive file and hand it to the existing source-creation
    pipeline, so it behaves identically to an uploaded source (same async
    processing, same content-core extraction, same embedding eligibility).

    `api.routers.sources` internals are imported lazily (function-local) to
    avoid making this module's import time depend on that router's full
    dependency chain (content_core, surreal_commands, ...) - mirrors this
    codebase's existing pattern of deferred imports for cross-module calls
    (see e.g. api/credentials_service.py's
    `from open_notebook.ai.models import Model`).
    """
    from api.routers.sources import (
        _build_content_state,
        _create_source_async_path,
        _write_uploaded_file,
    )

    # Notebook.get() raises NotFoundError itself when missing; the global
    # exception handlers in api/main.py map that to a 404.
    if not await Notebook.get(notebook_id):
        raise NotFoundError(f"Notebook {notebook_id} not found")

    conn = await _get_connected_connection()

    metadata = await _fetch_file_metadata(conn, file_id)
    mime_type = metadata.get("mimeType", "")
    drive_name = metadata.get("name") or file_id

    content, export_extension = await _download_file_bytes(conn, file_id, mime_type)
    filename = _drive_filename_with_extension(drive_name, mime_type, export_extension)

    file_path = await asyncio.to_thread(_write_uploaded_file, filename, content)

    source_data = SourceCreate(
        type="upload",
        notebooks=[notebook_id],
        title=drive_name,
        transformations=[],
        embed=False,
        delete_source=False,
        async_processing=True,
    )

    content_state = await _build_content_state(source_data, file_path)
    result = await _create_source_async_path(source_data, content_state, [], file_path)
    await _mark_synced_best_effort(conn)
    return result
