"""
Pytest configuration file.

This file ensures that the project root is in the Python path,
allowing tests to import from the api and open_notebook modules.
"""

import os
import sys
from pathlib import Path

# Ensure password auth is disabled for tests BEFORE any imports
# The PasswordAuthMiddleware skips auth when this env var is not set
# Set to empty string instead of deleting to prevent it from being reloaded
os.environ["OPEN_NOTEBOOK_PASSWORD"] = ""

# Load environment variables from .env file
# This must be done BEFORE any imports that depend on environment variables
from dotenv import load_dotenv

# Load .env file from project root
dotenv_path = Path(__file__).parent.parent / ".env"
if dotenv_path.exists():
    load_dotenv(dotenv_path)
    print(f"Loaded environment variables from {dotenv_path}")
else:
    print(f"Warning: .env file not found at {dotenv_path}")

# Add the project root to the Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import pytest  # noqa: E402 - must follow the sys.path/env setup above


@pytest.fixture(autouse=True)
def _never_write_real_audit_log(request):
    """Safety net: no test may write to the real audit_log table by default.

    Found live (2026-10-06 QA pass): tests that mock only a code path's own
    domain object (e.g. test_credentials_api.py patches `Credential.get`,
    test_drive_connection_state.py patches the Drive-connection repo calls)
    never anticipated that P2's audit log would later add a
    `record_audit_event(...)` call to those same endpoints - it goes
    through `AuditLog.save()`, which neither test mocks, so it fell through
    to the REAL database (this suite's conftest loads the real .env/
    SURREAL_* vars - there is no separate test DB). Confirmed: running the
    full suite wrote real "credential_deleted" rows containing literal
    `<AsyncMock ...>` reprs, and real "drive_connected"/"drive_disconnected"
    rows, into the live dev instance's Privacy & Activity log.

    Autouse + yield-based default rather than per-test patching, so this
    protects every existing and future test automatically - a test that
    wants to exercise record_audit_event/AuditLog.save for real (the 3
    audit-log-specific test files) just re-patches the same target locally;
    `unittest.mock.patch` context managers/decorators correctly layer over
    an outer patch and restore it afterward, so this fixture never fights
    with that.
    """
    from unittest.mock import AsyncMock, patch

    with patch(
        "open_notebook.domain.audit_log.AuditLog.save", new=AsyncMock(return_value=None)
    ):
        yield
