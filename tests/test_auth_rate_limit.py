"""
Tests for the failed-login rate limiter added to PasswordAuthMiddleware
(api/auth.py) - P2 "Seguridad" item 6: closing the previously-unlimited
password-guessing gap against the single shared OPEN_NOTEBOOK_PASSWORD.

Uses a minimal standalone FastAPI app wrapping only PasswordAuthMiddleware
(not api.main.app) so these tests exercise the real middleware class
without any database/network dependency, and without interfering with the
module-level app singleton other test files import.

Note: FastAPI/Starlette build the middleware stack (instantiating each
middleware, including reading the env var in PasswordAuthMiddleware.__init__)
lazily on the first request, not at add_middleware() time - so the
OPEN_NOTEBOOK_PASSWORD env var must stay set for the test's duration, not
just while add_middleware() is called. `monkeypatch.setenv` (reverted at
test teardown, after requests have run) does this correctly.
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.auth import (
    _FAILED_LOGIN_MAX_ATTEMPTS,
    PasswordAuthMiddleware,
    _LoginRateLimiter,
)

TEST_PASSWORD = "correct-password"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("OPEN_NOTEBOOK_PASSWORD", TEST_PASSWORD)

    app = FastAPI()

    @app.get("/protected")
    async def protected():
        return {"ok": True}

    app.add_middleware(PasswordAuthMiddleware)

    # Audit writes are best-effort and unrelated to rate-limiting behavior;
    # stub them out so these tests don't depend on a database.
    with patch("api.audit_service.record_audit_event", new=AsyncMock()):
        yield TestClient(app)


class TestLoginRateLimiterUnit:
    """Pure unit tests of the sliding-window counter, no HTTP involved."""

    def test_not_locked_out_below_threshold(self):
        limiter = _LoginRateLimiter(max_attempts=3, window_seconds=60)
        limiter.record_failure("1.2.3.4")
        limiter.record_failure("1.2.3.4")
        assert limiter.is_locked_out("1.2.3.4") is False

    def test_locked_out_at_threshold(self):
        limiter = _LoginRateLimiter(max_attempts=3, window_seconds=60)
        for _ in range(3):
            limiter.record_failure("1.2.3.4")
        assert limiter.is_locked_out("1.2.3.4") is True

    def test_different_ips_tracked_independently(self):
        limiter = _LoginRateLimiter(max_attempts=2, window_seconds=60)
        limiter.record_failure("1.1.1.1")
        limiter.record_failure("1.1.1.1")
        assert limiter.is_locked_out("1.1.1.1") is True
        assert limiter.is_locked_out("2.2.2.2") is False

    def test_success_clears_the_counter(self):
        limiter = _LoginRateLimiter(max_attempts=2, window_seconds=60)
        limiter.record_failure("1.1.1.1")
        limiter.record_failure("1.1.1.1")
        assert limiter.is_locked_out("1.1.1.1") is True
        limiter.record_success("1.1.1.1")
        assert limiter.is_locked_out("1.1.1.1") is False

    def test_old_attempts_outside_window_are_pruned(self):
        limiter = _LoginRateLimiter(max_attempts=2, window_seconds=60)
        # Simulate attempts far enough in the past (monotonic time near 0)
        # to fall outside a 60s window from "now".
        limiter._attempts["1.1.1.1"].append(0.0)
        limiter._attempts["1.1.1.1"].append(0.0)
        assert limiter.is_locked_out("1.1.1.1") is False


class TestPasswordAuthMiddlewareRateLimiting:
    """Integration tests through a real (minimal) ASGI app."""

    def test_wrong_password_returns_401_below_threshold(self, client):
        response = client.get(
            "/protected", headers={"Authorization": "Bearer wrong"}
        )
        assert response.status_code == 401

    def test_locked_out_after_max_attempts(self, client):
        for _ in range(_FAILED_LOGIN_MAX_ATTEMPTS):
            response = client.get(
                "/protected", headers={"Authorization": "Bearer wrong"}
            )
            assert response.status_code == 401

        locked_response = client.get(
            "/protected", headers={"Authorization": "Bearer wrong"}
        )
        assert locked_response.status_code == 429
        assert "Retry-After" in locked_response.headers

    def test_locked_out_ip_cannot_succeed_even_with_right_password(self, client):
        for _ in range(_FAILED_LOGIN_MAX_ATTEMPTS):
            client.get("/protected", headers={"Authorization": "Bearer wrong"})

        response = client.get(
            "/protected",
            headers={"Authorization": f"Bearer {TEST_PASSWORD}"},
        )
        assert response.status_code == 429

    def test_successful_login_resets_the_counter(self, client):
        for _ in range(_FAILED_LOGIN_MAX_ATTEMPTS - 1):
            client.get("/protected", headers={"Authorization": "Bearer wrong"})

        ok_response = client.get(
            "/protected",
            headers={"Authorization": f"Bearer {TEST_PASSWORD}"},
        )
        assert ok_response.status_code == 200

        # Counter was reset by the success, so another wrong attempt alone
        # shouldn't trip the lockout.
        response = client.get(
            "/protected", headers={"Authorization": "Bearer wrong"}
        )
        assert response.status_code == 401
