import secrets
import time
from collections import defaultdict, deque
from typing import Deque, Dict, Optional

from fastapi import Request
from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from open_notebook.utils.encryption import get_secret_from_env

# Failed-login rate limiting (P2 "Seguridad"): closes an otherwise
# unlimited password-guessing gap against the single shared
# OPEN_NOTEBOOK_PASSWORD. Deliberately simple - in-memory, per-process,
# keyed on client IP - per AGENTS.md ("CORS is wide-open and auth is a
# simple password middleware - dev defaults, not production hardening"):
# this closes a cheap, real gap without building distributed rate limiting
# for what is explicitly a single-instance, household-scale app.
_FAILED_LOGIN_WINDOW_SECONDS = 15 * 60
_FAILED_LOGIN_MAX_ATTEMPTS = 10


class _LoginRateLimiter:
    """Sliding-window limiter for failed password attempts, per IP."""

    def __init__(
        self,
        max_attempts: int = _FAILED_LOGIN_MAX_ATTEMPTS,
        window_seconds: float = _FAILED_LOGIN_WINDOW_SECONDS,
    ) -> None:
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._attempts: Dict[str, Deque[float]] = defaultdict(deque)

    def _prune(self, key: str, now: float) -> None:
        bucket = self._attempts[key]
        cutoff = now - self.window_seconds
        while bucket and bucket[0] < cutoff:
            bucket.popleft()

    def is_locked_out(self, key: str) -> bool:
        now = time.monotonic()
        self._prune(key, now)
        return len(self._attempts[key]) >= self.max_attempts

    def record_failure(self, key: str) -> None:
        now = time.monotonic()
        self._prune(key, now)
        self._attempts[key].append(now)

    def record_success(self, key: str) -> None:
        self._attempts.pop(key, None)


class PasswordAuthMiddleware(BaseHTTPMiddleware):
    """
    Middleware to check password authentication for all API requests.
    Auth is fully disabled (no hardcoded default password) if
    OPEN_NOTEBOOK_PASSWORD is not set.
    Supports Docker secrets via OPEN_NOTEBOOK_PASSWORD_FILE.
    """

    def __init__(
        self, app: ASGIApp, excluded_paths: Optional[list[str]] = None
    ) -> None:
        super().__init__(app)
        self.password = get_secret_from_env("OPEN_NOTEBOOK_PASSWORD")
        self.excluded_paths: list[str] = excluded_paths or [
            "/",
            "/health",
            "/docs",
            "/openapi.json",
            "/redoc",
        ]
        self._login_limiter = _LoginRateLimiter()

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        # Skip authentication if no password is set
        if not self.password:
            return await call_next(request)

        # Skip authentication for excluded paths
        if request.url.path in self.excluded_paths:
            return await call_next(request)

        # Skip authentication for CORS preflight requests (OPTIONS)
        if request.method == "OPTIONS":
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"

        # Brute-force guard: a locked-out IP never reaches the password
        # comparison below, regardless of what it sends.
        if self._login_limiter.is_locked_out(client_ip):
            return JSONResponse(
                status_code=429,
                content={
                    "detail": "Too many failed login attempts. Try again later."
                },
                headers={"Retry-After": str(int(_FAILED_LOGIN_WINDOW_SECONDS))},
            )

        # Check authorization header
        auth_header = request.headers.get("Authorization")

        if not auth_header:
            return JSONResponse(
                status_code=401,
                content={"detail": "Missing authorization header"},
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Expected format: "Bearer {password}"
        try:
            scheme, credentials = auth_header.split(" ", 1)
            if scheme.lower() != "bearer":
                raise ValueError("Invalid authentication scheme")
        except ValueError:
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid authorization header format"},
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Check password (constant-time to avoid a timing side-channel)
        if not secrets.compare_digest(
            credentials.encode("utf-8"), self.password.encode("utf-8")
        ):
            self._login_limiter.record_failure(client_ip)
            await self._record_failed_login(client_ip)
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid password"},
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Password is correct: clear this IP's failure count and proceed.
        self._login_limiter.record_success(client_ip)
        response = await call_next(request)
        return response

    @staticmethod
    async def _record_failed_login(client_ip: str) -> None:
        """Best-effort audit write for a failed login - never raises or
        blocks the 401 response (audit_service.record_audit_event already
        swallows its own failures; imported lazily to avoid any import-time
        cost on every request)."""
        try:
            from api.audit_service import ACTION_LOGIN_FAILED, record_audit_event

            await record_audit_event(ACTION_LOGIN_FAILED, detail=f"from {client_ip}")
        except Exception as e:  # pragma: no cover - defensive
            logger.debug(f"Could not record failed-login audit event: {e}")


def is_request_authenticated(request: Request) -> bool:
    """Best-effort auth check for endpoints excluded from PasswordAuthMiddleware.

    /api/config and /api/auth/status must stay reachable before login (the
    frontend's ConnectionGuard needs dbStatus, and the login page needs to
    know whether a password is required, before any token exists) - but that
    doesn't mean everything they return should be handed to an unauthenticated
    caller. This lets such an endpoint gate part of its response on auth
    without blocking the whole request. Mirrors PasswordAuthMiddleware's own
    check; returns True (nothing to gate) when auth is disabled entirely.
    """
    password = get_secret_from_env("OPEN_NOTEBOOK_PASSWORD")
    if not password:
        return True

    auth_header = request.headers.get("Authorization")
    if not auth_header:
        return False

    try:
        scheme, credentials = auth_header.split(" ", 1)
        if scheme.lower() != "bearer":
            return False
    except ValueError:
        return False

    return secrets.compare_digest(
        credentials.encode("utf-8"), password.encode("utf-8")
    )
