"""Canonical job-status helpers for surreal_commands-backed background jobs.

Why this exists
---------------
``surreal_commands.CommandStatus`` is ``class CommandStatus(str, Enum)``. On
Python 3.11+ ``f"{CommandStatus.NEW}"`` renders as ``"CommandStatus.NEW"``
(not ``"new"``), so any status that was interpolated into a string leaked an
enum repr to the UI ("Source processing status: CommandStatus.NEW").

Every API boundary that returns a job status must go through
:func:`normalize_job_status`, which maps whatever the queue/DB holds onto a
small *public* vocabulary the frontend knows how to label:

    queued | processing | completed | failed | canceled | unknown

and every error text that leaves the API must go through
:func:`sanitize_job_error` so stack traces / connection strings / API keys
stored in ``command.error_message`` are never shown to clients.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any, Final, Literal, Optional

PublicJobStatus = Literal[
    "queued", "processing", "completed", "failed", "canceled", "unknown"
]

QUEUED: Final = "queued"
PROCESSING: Final = "processing"
COMPLETED: Final = "completed"
FAILED: Final = "failed"
CANCELED: Final = "canceled"
UNKNOWN: Final = "unknown"

PUBLIC_JOB_STATUSES: Final[tuple[str, ...]] = (
    QUEUED,
    PROCESSING,
    COMPLETED,
    FAILED,
    CANCELED,
    UNKNOWN,
)

ACTIVE_JOB_STATUSES: Final[frozenset[str]] = frozenset({QUEUED, PROCESSING})
TERMINAL_JOB_STATUSES: Final[frozenset[str]] = frozenset({COMPLETED, FAILED, CANCELED})

# Raw value (as stored by surreal_commands or used historically by this API)
# -> public value. surreal_commands' CommandStatus members are
# new/running/completed/failed/canceled; the rest are legacy/aliases.
_ALIASES: Final[dict[str, str]] = {
    "new": QUEUED,
    "queued": QUEUED,
    "pending": QUEUED,
    "submitted": QUEUED,
    "running": PROCESSING,
    "processing": PROCESSING,
    "in_progress": PROCESSING,
    "completed": COMPLETED,
    "complete": COMPLETED,
    "success": COMPLETED,
    "succeeded": COMPLETED,
    "done": COMPLETED,
    "failed": FAILED,
    "failure": FAILED,
    "error": FAILED,
    "canceled": CANCELED,
    "cancelled": CANCELED,
    "unknown": UNKNOWN,
}


def _raw_status_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, Enum):
        value = value.value
    text = str(value).strip().lower()
    if not text:
        return None
    # Defensive: a status that was already stringified via f"{enum}" on 3.11+
    # looks like "commandstatus.new" — keep only the member part.
    if "." in text:
        text = text.rsplit(".", 1)[-1]
    return text


def normalize_job_status(value: Any) -> str:
    """Map any raw job status (enum, str, None) to the public vocabulary.

    Never raises; anything unrecognised (including ``None``) is ``"unknown"``.
    """
    text = _raw_status_text(value)
    if text is None:
        return UNKNOWN
    return _ALIASES.get(text, UNKNOWN)


def normalize_optional_job_status(value: Any) -> Optional[str]:
    """Like :func:`normalize_job_status` but keeps ``None`` as ``None``.

    Used where "no job at all" (legacy sources, episodes without a command)
    is semantically different from "job in an unknown state".
    """
    if value is None:
        return None
    return normalize_job_status(value)


def is_active_job_status(value: Any) -> bool:
    return normalize_job_status(value) in ACTIVE_JOB_STATUSES


# --- error sanitisation ------------------------------------------------------

_MAX_ERROR_LEN: Final = 200

_SECRET_PATTERNS: Final[list[tuple[re.Pattern[str], Any]]] = [
    # scheme://user:password@host -> scheme://***@host
    (re.compile(r"([a-z][a-z0-9+.\-]*://)[^\s/@:]+:[^\s/@]+@", re.I), r"\1***@"),
    # Authorization headers / bearer tokens
    (re.compile(r"\b(bearer|basic)\s+[A-Za-z0-9._~+/=\-]{8,}", re.I), r"\1 ***"),
    # key=value style secrets
    (
        re.compile(
            r"\b(api[_-]?key|apikey|token|secret|password|passwd|pwd|access[_-]?key|"
            r"client[_-]?secret|authorization)\b(\s*[:=]\s*)(\"[^\"]*\"|'[^']*'|\S+)",
            re.I,
        ),
        r"\1\2***",
    ),
    # Common provider key shapes (OpenAI/Anthropic sk-..., Google AIza..., GitHub, Slack, AWS)
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}"), "***"),
    (re.compile(r"\bAIza[0-9A-Za-z_\-]{20,}"), "***"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"), "***"),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}"), "***"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "***"),
    # Enum reprs that slipped into messages
    (re.compile(r"\bCommandStatus\.([A-Z_]+)\b"), lambda m: m.group(1).lower()),
]


def sanitize_job_error(message: Any, limit: int = _MAX_ERROR_LEN) -> Optional[str]:
    """Return a client-safe version of a job error message.

    - drops Python tracebacks (keeps only the final exception line)
    - redacts credentials / API keys / bearer tokens / passwords in URLs
    - rewrites ``CommandStatus.X`` enum reprs
    - caps length at ``limit`` characters

    ``None``/empty input returns ``None``.
    """
    if message is None:
        return None
    text = str(message).strip()
    if not text:
        return None

    if "Traceback (most recent call last)" in text or '  File "' in text:
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        # The exception line is the last non-frame line of a traceback.
        candidates = [
            ln
            for ln in lines
            if not ln.startswith("File ")
            and not ln.startswith("Traceback")
            and not ln.startswith("^")
        ]
        text = candidates[-1] if candidates else ""
    else:
        # Multi-line messages: only the first line is meant for humans.
        text = text.splitlines()[0].strip()

    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)

    if not text:
        return None
    return text if len(text) <= limit else text[:limit] + "…"
