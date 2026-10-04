"""Google Drive connection state: DB is the single source of truth.

Covers the "toast says connected, Settings card says not connected" bug:
- DriveConnection must re-read the DB instead of trusting the per-process
  RecordModel singleton cache.
- GET /api/drive/status exposes configured/connected/scopes/dates/
  needs_reconnect and never tokens.
- OAuth callback success / cancel (error=access_denied, no code) / error.
- Refresh with invalid_grant flags needs_reconnect (no fake "connected").
- Disconnect revokes best-effort and clears local credentials.

No real DB or Google: repo_* calls hit an in-memory dict, Google HTTP goes
through httpx.MockTransport.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from api import drive_service
from open_notebook.domain.drive_connection import DriveConnection

RECORD = "open_notebook:drive_connection"
SCOPES = (
    "https://www.googleapis.com/auth/drive.readonly "
    "https://www.googleapis.com/auth/userinfo.email"
)


class FakeDB:
    """Minimal stand-in for the schemaless `open_notebook` singleton row.
    UPSERT ... MERGE semantics: None removes the field (SurrealDB NONE)."""

    def __init__(self) -> None:
        self.row: Dict[str, Any] = {}
        self.upserts = 0

    async def repo_query(self, query: str, vars: Dict[str, Any] | None = None):
        return [dict(self.row, id=RECORD)] if self.row or "ONLY" not in query else []

    async def repo_upsert(self, table: str, id: str, data: Dict[str, Any], *a, **k):
        assert id == RECORD
        self.upserts += 1
        for key, value in data.items():
            if value is None:
                self.row.pop(key, None)
            else:
                self.row[key] = value
        return [dict(self.row)]


@pytest.fixture
def db(monkeypatch):
    fake = FakeDB()
    monkeypatch.setattr("open_notebook.domain.drive_connection.repo_query", fake.repo_query)
    monkeypatch.setattr("open_notebook.domain.base.repo_query", fake.repo_query)
    monkeypatch.setattr("open_notebook.domain.base.repo_upsert", fake.repo_upsert)
    # Reversible fake encryption - no OPEN_NOTEBOOK_ENCRYPTION_KEY needed.
    monkeypatch.setattr(
        "open_notebook.domain.drive_connection.encrypt_value", lambda v: f"enc:{v}"
    )
    monkeypatch.setattr(
        "open_notebook.domain.drive_connection.decrypt_value",
        lambda v: v[4:] if v.startswith("enc:") else v,
    )
    DriveConnection.clear_instance()
    yield fake
    DriveConnection.clear_instance()


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("GOOGLE_DRIVE_CLIENT_ID", "client-id")
    monkeypatch.setenv("GOOGLE_DRIVE_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("GOOGLE_DRIVE_REDIRECT_URI", "http://localhost:5055/api/drive/callback")
    monkeypatch.delenv("GOOGLE_DRIVE_FRONTEND_URL", raising=False)


@pytest.fixture
def google(monkeypatch):
    """Route every httpx.AsyncClient in drive_service through a handler."""
    calls: List[httpx.Request] = []
    state: Dict[str, Callable[[httpx.Request], httpx.Response]] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return state["handler"](request)

    real_client = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(drive_service.httpx, "AsyncClient", factory)

    class G:
        requests = calls

        @staticmethod
        def set(fn):
            state["handler"] = fn

    return G


@pytest.fixture
def client():
    from api.main import app

    return TestClient(app, follow_redirects=False)


def _connected_row(**overrides) -> Dict[str, Any]:
    row = {
        "access_token": "enc:access-1",
        "refresh_token": "enc:refresh-1",
        "token_expiry": datetime.now(timezone.utc) + timedelta(hours=1),
        "account_email": "ana@example.com",
        "connected_at": datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc),
        "granted_scopes": SCOPES,
    }
    row.update(overrides)
    return row


def _assert_cleared(row: Dict[str, Any]) -> None:
    for key in (
        "access_token",
        "refresh_token",
        "token_expiry",
        "account_email",
        "connected_at",
        "granted_scopes",
        "last_sync_at",
        "reconnect_reason",
    ):
        assert key not in row, key
    assert not row.get("needs_reconnect")


def _redirect_query(response) -> Dict[str, List[str]]:
    assert response.status_code in (302, 307)
    location = response.headers["location"]
    assert urlparse(location).path == "/settings"
    return parse_qs(urlparse(location).query)


# --- status ---------------------------------------------------------------


class TestStatus:
    def test_status_rereads_db_not_stale_singleton(self, db, configured, client):
        # Singleton loaded once while the DB row was empty...
        first = client.get("/api/drive/status").json()
        assert first["connected"] is False

        # ...then another process (the OAuth callback) writes the row.
        db.row = _connected_row()
        second = client.get("/api/drive/status").json()
        assert second["connected"] is True
        assert second["account_email"] == "ana@example.com"

        # And a disconnect elsewhere (fields removed) must not leave stale
        # in-memory values behind.
        db.row = {}
        third = client.get("/api/drive/status").json()
        assert third["connected"] is False
        assert third["account_email"] is None

    def test_status_shape_and_no_tokens(self, db, configured, client):
        db.row = _connected_row(
            last_sync_at=datetime(2026, 10, 2, 9, 30, tzinfo=timezone.utc)
        )
        data = client.get("/api/drive/status").json()
        assert data == {
            "connected": True,
            "configured": True,
            "account_email": "ana@example.com",
            "granted_scopes": SCOPES.split(),
            "connected_at": "2026-10-01T12:00:00+00:00",
            "last_sync_at": "2026-10-02T09:30:00+00:00",
            "needs_reconnect": False,
            "reconnect_reason": None,
        }
        body = client.get("/api/drive/status").text
        assert "access-1" not in body and "refresh-1" not in body

    def test_status_not_configured_is_200(self, db, monkeypatch, client):
        monkeypatch.delenv("GOOGLE_DRIVE_CLIENT_ID", raising=False)
        response = client.get("/api/drive/status")
        assert response.status_code == 200
        assert response.json()["configured"] is False

    def test_status_needs_reconnect_is_not_connected(self, db, configured, client):
        db.row = {
            "account_email": "ana@example.com",
            "needs_reconnect": True,
            "reconnect_reason": "revoked",
        }
        data = client.get("/api/drive/status").json()
        assert data["connected"] is False
        assert data["needs_reconnect"] is True
        assert data["reconnect_reason"] == "revoked"
        assert data["account_email"] == "ana@example.com"

    def test_legacy_record_without_new_fields(self, db, configured, client):
        """Record written by migration 26 / old code: new fields absent."""
        db.row = {
            "access_token": "enc:a",
            "refresh_token": "enc:r",
            "token_expiry": "2030-01-01T00:00:00Z",
            "account_email": "x@example.com",
            "connected_at": "2026-09-01T00:00:00Z",
        }
        data = client.get("/api/drive/status").json()
        assert data["connected"] is True
        assert data["granted_scopes"] == []
        assert data["last_sync_at"] is None
        assert data["connected_at"] == "2026-09-01T00:00:00+00:00"


# --- callback -------------------------------------------------------------


def _token_and_userinfo(scope: str = SCOPES):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com" and request.url.path == "/token":
            return httpx.Response(
                200,
                json={
                    "access_token": "access-new",
                    "refresh_token": "refresh-new",
                    "expires_in": 3599,
                    "scope": scope,
                    "token_type": "Bearer",
                },
            )
        if "userinfo" in request.url.path:
            return httpx.Response(200, json={"email": "ana@example.com"})
        return httpx.Response(404)

    return handler


class TestCallback:
    def test_success_persists_and_redirects(self, db, configured, google, client):
        google.set(_token_and_userinfo())
        response = client.get("/api/drive/callback", params={"code": "auth-code"})
        assert _redirect_query(response) == {"drive": ["connected"]}

        assert db.row["access_token"] == "enc:access-new"
        assert db.row["refresh_token"] == "enc:refresh-new"
        assert db.row["granted_scopes"] == SCOPES
        assert db.row["account_email"] == "ana@example.com"
        assert "connected_at" in db.row

        status = client.get("/api/drive/status").json()
        assert status["connected"] is True
        assert status["granted_scopes"] == SCOPES.split()

    def test_cancelled_without_code_does_not_422(self, db, configured, google, client):
        response = client.get("/api/drive/callback", params={"error": "access_denied"})
        assert _redirect_query(response) == {"drive": ["cancelled"]}
        assert google.requests == []
        assert db.upserts == 0

    def test_other_google_error(self, db, configured, client):
        response = client.get("/api/drive/callback", params={"error": "server_error"})
        q = _redirect_query(response)
        assert q["drive"] == ["error"]
        assert "server_error" in q["message"][0]

    def test_missing_code_and_error(self, db, configured, client):
        q = _redirect_query(client.get("/api/drive/callback"))
        assert q["drive"] == ["error"]

    def test_token_exchange_rejected(self, db, configured, google, client):
        google.set(lambda r: httpx.Response(400, json={"error": "invalid_grant"}))
        q = _redirect_query(client.get("/api/drive/callback", params={"code": "old"}))
        assert q["drive"] == ["error"]
        assert db.row == {}

    def test_network_error(self, db, configured, google, client):
        def boom(request):
            raise httpx.ConnectError("offline", request=request)

        google.set(boom)
        q = _redirect_query(client.get("/api/drive/callback", params={"code": "c"}))
        assert q["drive"] == ["error"]
        assert db.row == {}

    def test_db_failure_is_not_reported_as_connected(
        self, db, configured, google, client, monkeypatch
    ):
        google.set(_token_and_userinfo())

        async def failing_upsert(*a, **k):
            raise RuntimeError("db down")

        monkeypatch.setattr("open_notebook.domain.base.repo_upsert", failing_upsert)
        q = _redirect_query(client.get("/api/drive/callback", params={"code": "c"}))
        assert q["drive"] == ["error"]

    def test_drive_scope_not_granted(self, db, configured, google, client):
        google.set(
            _token_and_userinfo(scope="https://www.googleapis.com/auth/userinfo.email")
        )
        q = _redirect_query(client.get("/api/drive/callback", params={"code": "c"}))
        assert q["drive"] == ["error"]
        status = client.get("/api/drive/status").json()
        assert status["connected"] is False
        assert status["needs_reconnect"] is True
        assert status["reconnect_reason"] == "missing_scope"

    def test_frontend_url_override(self, db, configured, client, monkeypatch):
        monkeypatch.setenv("GOOGLE_DRIVE_FRONTEND_URL", "http://localhost:3000/")
        response = client.get("/api/drive/callback", params={"error": "access_denied"})
        assert response.headers["location"] == "http://localhost:3000/settings?drive=cancelled"


# --- refresh / revoked ----------------------------------------------------


class TestRefresh:
    def test_expired_token_is_refreshed_and_sync_recorded(
        self, db, configured, google, client
    ):
        db.row = _connected_row(token_expiry=datetime.now(timezone.utc) - timedelta(minutes=5))

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/token":
                return httpx.Response(200, json={"access_token": "access-2", "expires_in": 3600})
            assert request.headers["Authorization"] == "Bearer access-2"
            return httpx.Response(200, json={"files": [{"id": "f1", "name": "Doc"}]})

        google.set(handler)
        response = client.get("/api/drive/files")
        assert response.status_code == 200
        assert response.json()["files"][0]["id"] == "f1"
        assert db.row["access_token"] == "enc:access-2"
        assert db.row["refresh_token"] == "enc:refresh-1"  # preserved
        assert "last_sync_at" in db.row

    def test_invalid_grant_marks_needs_reconnect(self, db, configured, google, client):
        db.row = _connected_row(token_expiry=datetime.now(timezone.utc) - timedelta(minutes=5))
        google.set(
            lambda r: httpx.Response(
                400, json={"error": "invalid_grant", "error_description": "Token has been expired or revoked."}
            )
        )
        response = client.get("/api/drive/files")
        # 422, never 401: a 401 would log the user out of the whole app.
        assert response.status_code == 422

        assert "access_token" not in db.row
        assert "refresh_token" not in db.row
        status = client.get("/api/drive/status").json()
        assert status["connected"] is False
        assert status["needs_reconnect"] is True
        assert status["reconnect_reason"] == "revoked"
        assert status["account_email"] == "ana@example.com"

    def test_drive_401_forces_one_refresh(self, db, configured, google, client):
        db.row = _connected_row()
        seen: List[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/token":
                return httpx.Response(200, json={"access_token": "access-3", "expires_in": 3600})
            seen.append(request.headers["Authorization"])
            if request.headers["Authorization"] == "Bearer access-1":
                return httpx.Response(401)
            return httpx.Response(200, json={"files": []})

        google.set(handler)
        assert client.get("/api/drive/files").status_code == 200
        assert seen == ["Bearer access-1", "Bearer access-3"]

    def test_not_connected_list_is_422_not_401(self, db, configured, client):
        assert client.get("/api/drive/files").status_code == 422

    def test_reconnect_clears_flag(self, db, configured, google, client):
        db.row = {"account_email": "ana@example.com", "needs_reconnect": True, "reconnect_reason": "revoked"}
        google.set(_token_and_userinfo())
        q = _redirect_query(client.get("/api/drive/callback", params={"code": "c"}))
        assert q == {"drive": ["connected"]}
        status = client.get("/api/drive/status").json()
        assert status["connected"] is True
        assert status["needs_reconnect"] is False
        assert status["reconnect_reason"] is None


# --- disconnect -----------------------------------------------------------


class TestDisconnect:
    def test_disconnect_revokes_and_clears(self, db, configured, google, client):
        db.row = _connected_row(last_sync_at=datetime.now(timezone.utc))
        google.set(lambda r: httpx.Response(200))
        response = client.delete("/api/drive/disconnect")
        assert response.status_code == 200
        _assert_cleared(db.row)
        revoke = [r for r in google.requests if r.url.path == "/revoke"]
        assert len(revoke) == 1
        assert b"token=refresh-1" in revoke[0].content
        assert client.get("/api/drive/status").json()["connected"] is False

    def test_disconnect_clears_even_if_revoke_fails(self, db, configured, google, client):
        db.row = _connected_row()

        def boom(request):
            raise httpx.ConnectError("offline", request=request)

        google.set(boom)
        assert client.delete("/api/drive/disconnect").status_code == 200
        _assert_cleared(db.row)
