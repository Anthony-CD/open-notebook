"""
Google Drive connection domain model.

A singleton record holding the OAuth connection this Open Notebook instance
uses to browse/import files from a single Google Drive account. This app is
single-user/couple-scale (see AGENTS.md), not multi-tenant, so there is
exactly one connection for the whole instance - the same singleton pattern
used by DefaultModels/ContentSettings/ProviderConfig
(open_notebook/domain/base.py RecordModel, open_notebook/ai/models.py,
open_notebook/domain/content_settings.py), sharing their `open_notebook`
singleton-records table via the fixed id `open_notebook:drive_connection`
(migration 26).

This is deliberately NOT the Credential model (open_notebook/domain/credential.py):
Credential is shaped for static, form-entered API keys for AI providers.
A Drive connection is fundamentally different - an access_token/refresh_token
pair obtained via a redirect-based OAuth flow, with a token_expiry that must
be checked (and the access_token refreshed) on every use.

access_token/refresh_token are stored Fernet-encrypted at rest using the same
helper as AI provider credentials (open_notebook/utils/encryption.py). Unlike
Credential (which decrypts eagerly in get()/get_all() and exposes plaintext
fields), the fields on this model always hold ciphertext once persisted -
callers must go through decrypt_access_token()/decrypt_refresh_token() to get
plaintext, and store_tokens() to write new (plaintext) tokens. This mirrors
RecordModel's raw object.__setattr__ load path (no field validators run), so
keeping "the field is ciphertext" as an invariant is simpler than trying to
decrypt transparently on load.
"""

from datetime import datetime, timezone
from typing import Any, ClassVar, Dict, Optional, cast

from open_notebook.database.repository import ensure_record_id, repo_query
from open_notebook.domain.base import RecordModel
from open_notebook.utils.encryption import decrypt_value, encrypt_value


def as_utc_datetime(value: Any) -> Optional[datetime]:
    """Normalize a datetime loaded from SurrealDB. RecordModel loads raw DB
    values without validation, so depending on the driver a datetime field may
    come back as a datetime, an ISO string, or None."""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


class DriveConnection(RecordModel):
    record_id: ClassVar[str] = "open_notebook:drive_connection"

    access_token: Optional[str] = None
    refresh_token: Optional[str] = None
    token_expiry: Optional[datetime] = None
    account_email: Optional[str] = None
    connected_at: Optional[datetime] = None
    # Added after migration 26 - the `open_notebook` table is schemaless, so
    # these are purely additive: an existing record simply lacks them and
    # load_fresh() falls back to the defaults below (no migration needed).
    granted_scopes: Optional[str] = None  # space-separated `scope` from Google
    last_sync_at: Optional[datetime] = None
    needs_reconnect: Optional[bool] = False
    reconnect_reason: Optional[str] = None

    @classmethod
    async def get_instance(cls) -> "DriveConnection":
        """Return the connection with state re-read from the database.

        RecordModel.get_instance() caches `_db_loaded=True` per process and
        never re-reads, so after an OAuth callback / disconnect handled by a
        different process (or a token revocation detected elsewhere), the
        cached singleton would keep serving stale state - the "toast says
        connected, card says not connected" bug. The DB is the single source
        of truth for the Drive connection, so always reload (load_fresh()).
        Other RecordModel subclasses keep the base caching behavior.
        """
        return await cls.load_fresh()

    @classmethod
    async def load_fresh(cls) -> "DriveConnection":
        """Read the record from the DB and overwrite every field on the
        singleton in one synchronous step (no await between reset and assign,
        so concurrent coroutines never observe a half-reset instance). Fields
        missing from the row (e.g. removed by a MERGE with NONE) are reset to
        their defaults instead of keeping a stale in-memory value."""
        result = await repo_query(
            "SELECT * FROM ONLY $record_id",
            {"record_id": ensure_record_id(cls.record_id)},
        )
        row: Dict[str, Any] = {}
        if isinstance(result, list) and result and isinstance(result[0], dict):
            row = result[0]
        elif isinstance(result, dict):
            row = result

        instance = cast("DriveConnection", cls())
        for name, field in cls.model_fields.items():
            value = row.get(name, field.default)
            object.__setattr__(instance, name, value)
        object.__setattr__(instance, "_db_loaded", True)
        return instance

    @property
    def is_connected(self) -> bool:
        return bool(self.access_token and self.refresh_token)

    def decrypt_access_token(self) -> Optional[str]:
        if not self.access_token:
            return None
        return decrypt_value(self.access_token)

    def decrypt_refresh_token(self) -> Optional[str]:
        if not self.refresh_token:
            return None
        return decrypt_value(self.refresh_token)

    def is_access_token_expired(self, skip_seconds: int = 60) -> bool:
        """True if the access token is missing, expired, or expiring within
        `skip_seconds` - refresh proactively instead of racing a Drive API call
        against the actual expiry."""
        if not self.access_token or not self.token_expiry:
            return True
        expiry = as_utc_datetime(self.token_expiry)
        if expiry is None:
            return True
        if expiry.tzinfo is None:
            # RecordModel loads DB values via object.__setattr__ with no field
            # validation (see base.py _load_from_db); be defensive about
            # naive datetimes rather than assume the driver always returns
            # timezone-aware ones.
            expiry = expiry.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        return (expiry - now).total_seconds() <= skip_seconds

    @property
    def scope_list(self) -> list[str]:
        return [s for s in (self.granted_scopes or "").split() if s]

    async def store_tokens(
        self,
        *,
        access_token: str,
        refresh_token: Optional[str],
        token_expiry: datetime,
        account_email: Optional[str] = None,
        granted_scopes: Optional[str] = None,
        new_connection: bool = False,
    ) -> None:
        """Encrypt and persist tokens.

        `refresh_token` is optional because a plain token *refresh* call
        never returns one - preserve the existing encrypted refresh_token
        when none is supplied. `account_email`/`granted_scopes` are optional
        so a token-refresh-only call doesn't have to re-pass them.
        `new_connection=True` (OAuth callback) resets connected_at and clears
        any needs-reconnect flag; a successful refresh also clears the flag.
        """
        self.access_token = encrypt_value(access_token)
        if refresh_token:
            self.refresh_token = encrypt_value(refresh_token)
        self.token_expiry = token_expiry
        if account_email:
            self.account_email = account_email
        if granted_scopes:
            self.granted_scopes = granted_scopes
        if new_connection or not self.connected_at:
            self.connected_at = datetime.now(timezone.utc)
        self.needs_reconnect = False
        self.reconnect_reason = None
        await self.update()

    async def mark_needs_reconnect(self, reason: str) -> None:
        """Google refused our refresh token (revoked / expired grant), or the
        user did not grant the Drive scope. Drop the unusable tokens so the
        UI never shows a fake "connected", but keep account_email so the card
        can say which account needs reconnecting."""
        self.access_token = None
        self.refresh_token = None
        self.token_expiry = None
        self.needs_reconnect = True
        self.reconnect_reason = reason
        await self.update()

    async def mark_synced(self) -> None:
        self.last_sync_at = datetime.now(timezone.utc)
        await self.update()

    async def disconnect(self) -> None:
        """Clear the connection record (DELETE /api/drive/disconnect)."""
        self.access_token = None
        self.refresh_token = None
        self.token_expiry = None
        self.account_email = None
        self.connected_at = None
        self.granted_scopes = None
        self.last_sync_at = None
        self.needs_reconnect = False
        self.reconnect_reason = None
        await self.update()
