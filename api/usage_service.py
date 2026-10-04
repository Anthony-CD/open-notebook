import os
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from open_notebook.ai.pricing import STATIC_PRICE_TABLE
from open_notebook.domain.usage import UsageEvent, aggregate_events
from open_notebook.exceptions import InvalidInputError

DEFAULT_STUDY_BUDGET_USD = 10.0

PRESET_DAYS = {"7d": 7, "30d": 30, "365d": 365}
# Backward compatibility with the old `period=month|year` query values.
LEGACY_PRESETS = {"month": "30d", "year": "365d"}
VALID_PRESETS = set(PRESET_DAYS) | {"all"} | set(LEGACY_PRESETS)


def _parse_dt(value: Optional[str], name: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as e:
        raise InvalidInputError(f"Invalid '{name}' datetime: {value}") from e
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def resolve_range(
    period: Optional[str],
    date_from: Optional[str],
    date_to: Optional[str],
    now: Optional[datetime] = None,
) -> Tuple[str, Optional[datetime], Optional[datetime]]:
    """Resolve (preset, since, until). Explicit from/to win over a preset."""
    now = now or datetime.now(timezone.utc)
    since = _parse_dt(date_from, "from")
    until = _parse_dt(date_to, "to")
    if since is not None or until is not None:
        if since is not None and until is not None and since > until:
            raise InvalidInputError("'from' must be before 'to'")
        return "custom", since, until

    preset = period or "30d"
    if preset not in VALID_PRESETS:
        raise InvalidInputError(f"Invalid period: {preset}")
    preset = LEGACY_PRESETS.get(preset, preset)
    if preset == "all":
        return preset, None, None
    return preset, now - timedelta(days=PRESET_DAYS[preset]), None


def _budget_usd() -> float:
    try:
        return float(os.getenv("STUDY_BUDGET_USD", DEFAULT_STUDY_BUDGET_USD))
    except ValueError:
        return DEFAULT_STUDY_BUDGET_USD


async def get_usage_summary(
    period: Optional[str] = "30d",
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    model: Optional[str] = None,
    provider: Optional[str] = None,
    task_type: Optional[str] = None,
) -> dict:
    """Aggregate LLM usage/cost from usage_event rows for the selection."""
    preset, since, until = resolve_range(period, date_from, date_to)
    rows = await UsageEvent.fetch_rows(since, until)
    summary = aggregate_events(
        rows, model=model or None, provider=provider or None, task_type=task_type or None
    )
    return {
        **summary,
        "period": {
            "preset": preset,
            "from": since.isoformat() if since else None,
            "to": until.isoformat() if until else None,
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "budget_usd": _budget_usd(),
        "applied_filters": {
            "model": model or None,
            "provider": provider or None,
            "task_type": task_type or None,
        },
        "pricing_table_version": STATIC_PRICE_TABLE["version"],
    }
