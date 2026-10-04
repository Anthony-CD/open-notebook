import os
from datetime import datetime, timedelta, timezone
from typing import Any, ClassVar, Dict, Iterable, List, Optional

from loguru import logger

from open_notebook.database.repository import repo_query
from open_notebook.domain.base import ObjectModel

# Mirrors api/usage_service.py's STUDY_BUDGET_USD default - duplicated (not
# imported) because open_notebook/domain must not depend on api/ (layering:
# api -> open_notebook, never the reverse). Budget-aware fallback selection
# (open_notebook/ai/models.py) needs this from the domain layer.
STUDY_BUDGET_ENV_VAR = "STUDY_BUDGET_USD"
DEFAULT_STUDY_BUDGET_USD = 10.0

COST_STATUSES = ("known", "free", "unknown")

# Embedding calls do not go through provision_langchain_model(), so no
# usage_event is ever written for them. Reported explicitly in the summary
# instead of showing a fake 0.
EMBEDDINGS_TRACKED = False


class UsageEvent(ObjectModel):
    """One LLM call's token usage / estimated cost.

    Written by the callback handler `provision_langchain_model()` attaches
    to every provisioned chat model (see open_notebook/ai/provision.py) -
    chat, transformations, podcasts and study tools all flow through that
    single choke point, so no per-call-site instrumentation is needed.

    Cost semantics (migration 28):
    - `estimated_cost_usd` is None (field absent) when the price is unknown.
      It is never stored as a fake 0 for unknown prices.
    - `cost_status` is 'known' | 'free' | 'unknown'.
    - `pricing_version` identifies the price source used.
    - `cached_input_tokens` is None when the provider didn't report it.
    - `failed` marks calls that ended in on_llm_error.
    Legacy rows (pre-28, no cost_status) are classified on read by
    `classify_row()`.
    """

    table_name: ClassVar[str] = "usage_event"

    provider: str
    model_name: str
    task_type: str
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: Optional[int] = None
    estimated_cost_usd: Optional[float] = None
    cost_status: str = "unknown"
    pricing_version: Optional[str] = None
    failed: bool = False
    error_type: Optional[str] = None

    # ------------------------------------------------------------------
    # Legacy aggregate (kept for compatibility; known-cost rows only)
    # ------------------------------------------------------------------
    @classmethod
    async def get_summary(cls, since: datetime) -> Dict[str, Any]:
        """Legacy aggregate: known cost + tokens since a cutoff.

        Kept for backward compatibility; the Cost Meter uses
        `aggregate_events()` / `get_detailed_summary()` instead. Rows with
        unknown cost (estimated_cost_usd = NONE) are excluded from the sums
        because SurrealDB's math::sum() errors on NONE array elements.
        The totals query must keep `GROUP ALL` (see test_model_fallback.py).
        """
        total_cost_usd = 0.0
        input_tokens = 0
        output_tokens = 0
        try:
            totals_result = await repo_query(
                "SELECT "
                "math::sum(estimated_cost_usd) AS total_cost_usd, "
                "math::sum(input_tokens) AS input_tokens, "
                "math::sum(output_tokens) AS output_tokens "
                "FROM usage_event WHERE created >= $since "
                "AND estimated_cost_usd != NONE GROUP ALL;",
                {"since": since},
            )
            if totals_result:
                row = totals_result[0]
                total_cost_usd = float(row.get("total_cost_usd") or 0.0)
                input_tokens = int(row.get("input_tokens") or 0)
                output_tokens = int(row.get("output_tokens") or 0)
        except Exception as e:
            logger.error(f"Error aggregating usage totals: {e}")

        by_task_type: Dict[str, float] = {}
        try:
            grouped_result = await repo_query(
                "SELECT task_type, math::sum(estimated_cost_usd) AS cost "
                "FROM usage_event WHERE created >= $since "
                "AND estimated_cost_usd != NONE GROUP BY task_type;",
                {"since": since},
            )
            for row in grouped_result:
                task_type = row.get("task_type")
                if task_type:
                    by_task_type[task_type] = float(row.get("cost") or 0.0)
        except Exception as e:
            logger.error(f"Error aggregating usage by task_type: {e}")

        return {
            "total_cost_usd": total_cost_usd,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "by_task_type": by_task_type,
        }

    # ------------------------------------------------------------------
    # Row fetch + aggregation (single source of truth: usage_event rows)
    # ------------------------------------------------------------------
    @classmethod
    async def fetch_rows(
        cls, since: Optional[datetime], until: Optional[datetime]
    ) -> List[Dict[str, Any]]:
        """Fetch the projected usage_event rows in a time range.

        Aggregation happens in Python (see aggregate_events) rather than in
        SurrealQL because cost and cached-token fields are optional (NONE)
        on unknown/legacy rows and math::sum() errors on NONE elements.
        Rows are small (a handful of scalar fields) and this is a
        single-user app, so a full fetch of the range is acceptable.
        Raises on DB errors - callers decide how to degrade.
        """
        clauses: List[str] = []
        params: Dict[str, Any] = {}
        if since is not None:
            clauses.append("created >= $since")
            params["since"] = since
        if until is not None:
            clauses.append("created <= $until")
            params["until"] = until
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        query = (
            "SELECT provider, model_name, task_type, input_tokens, output_tokens, "
            "cached_input_tokens, estimated_cost_usd, cost_status, failed, created "
            f"FROM usage_event{where};"
        )
        result = await repo_query(query, params)
        return list(result or [])

    @classmethod
    async def get_current_spend_usd(cls, window_days: int = 30) -> float:
        """Total *known* estimated cost over the trailing `window_days`.

        Unknown-cost events are not counted (their cost is not knowable);
        free events add 0. Used by the budget-aware fallback cutoff in
        open_notebook/ai/models.py. Errors propagate so
        `is_within_budget()` can fail closed.
        """
        since = datetime.now(timezone.utc) - timedelta(days=window_days)
        rows = await cls.fetch_rows(since, None)
        return aggregate_events(rows)["cost"]["known_usd"]

    @classmethod
    async def is_within_budget(cls, window_days: int = 30) -> bool:
        """Whether trailing spend is still under STUDY_BUDGET_USD (default $10).

        Best-effort: any failure reading spend is treated as "not within
        budget" (fail closed) so a broken budget check can never silently let
        paid-fallback spend run away - it can only ever be overly cautious
        and skip a paid fallback that would have been affordable.
        """
        try:
            budget = float(
                os.getenv(STUDY_BUDGET_ENV_VAR, str(DEFAULT_STUDY_BUDGET_USD))
            )
            spend = await cls.get_current_spend_usd(window_days=window_days)
            return spend < budget
        except Exception as e:
            logger.warning(f"Budget check failed, treating as over-budget: {e}")
            return False


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _float_or_none(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def classify_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize one usage_event row into (cost_status, cost) honestly.

    - Rows with an explicit cost_status (written after migration 28, or
      backfilled by it) are trusted.
    - Legacy rows without cost_status: a positive stored cost is 'known'; an
      explicitly free model is 'free'; a 0 cost is otherwise 'unknown' (the
      old pricing code stored 0 for every non-OpenRouter or missing price).
    - Unknown rows are re-priced from the static price table when it has
      the model, so prices added later still apply to older events.
    """
    from open_notebook.ai.pricing import (
        classify_free,
        compute_cost,
        static_price,
    )

    provider = row.get("provider") or ""
    model_name = row.get("model_name") or ""
    input_tokens = _int(row.get("input_tokens"))
    output_tokens = _int(row.get("output_tokens"))
    cached_raw = row.get("cached_input_tokens")
    cached = _int(cached_raw) if cached_raw is not None else None
    cost = _float_or_none(row.get("estimated_cost_usd"))
    status = row.get("cost_status")

    if status not in COST_STATUSES:
        if cost is not None and cost > 0:
            status = "known"
        elif classify_free(provider, model_name):
            status = "free"
            cost = 0.0
        else:
            status = "unknown"
            cost = None

    if status == "free":
        cost = 0.0
    elif status == "known" and cost is None:
        status = "unknown"

    if status == "unknown":
        price = static_price(provider, model_name)
        if price is not None:
            cost = compute_cost(price, input_tokens, output_tokens, cached)
            status = "known" if cost is not None else "unknown"
        else:
            cost = None

    return {
        "provider": provider or "unknown",
        "model_name": model_name or "unknown",
        "task_type": row.get("task_type") or "unknown",
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cached_input_tokens": cached,
        "cost_status": status,
        "cost_usd": cost,
        "failed": bool(row.get("failed") or False),
    }


def _empty_bucket() -> Dict[str, Any]:
    return {
        "event_count": 0,
        "failed_count": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cached_input_tokens": 0,
        "cost_known_usd": 0.0,
        "known_cost_events": 0,
        "free_events": 0,
        "unknown_cost_events": 0,
    }


def _add(bucket: Dict[str, Any], ev: Dict[str, Any]) -> None:
    bucket["event_count"] += 1
    if ev["failed"]:
        bucket["failed_count"] += 1
    bucket["input_tokens"] += ev["input_tokens"]
    bucket["output_tokens"] += ev["output_tokens"]
    bucket["cached_input_tokens"] += ev["cached_input_tokens"] or 0
    if ev["cost_status"] == "known":
        bucket["known_cost_events"] += 1
        bucket["cost_known_usd"] += ev["cost_usd"] or 0.0
    elif ev["cost_status"] == "free":
        bucket["free_events"] += 1
    else:
        bucket["unknown_cost_events"] += 1


def _sorted_breakdown(groups: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    items = [{"key": key, **bucket} for key, bucket in groups.items()]
    for item in items:
        item["cost_known_usd"] = round(item["cost_known_usd"], 10)
    items.sort(
        key=lambda i: (
            -i["cost_known_usd"],
            -(i["input_tokens"] + i["output_tokens"]),
            i["key"],
        )
    )
    return items


def aggregate_events(
    rows: Iterable[Dict[str, Any]],
    model: Optional[str] = None,
    provider: Optional[str] = None,
    task_type: Optional[str] = None,
) -> Dict[str, Any]:
    """Aggregate usage_event rows into the Cost Meter summary shape.

    Filter options (available models/providers/task types) are computed
    from the rows *before* the model/provider/task_type filters are applied,
    so the UI can always offer every value present in the period.
    """
    normalized = [classify_row(r) for r in rows]

    options = {
        "models": sorted({ev["model_name"] for ev in normalized}),
        "providers": sorted({ev["provider"] for ev in normalized}),
        "task_types": sorted({ev["task_type"] for ev in normalized}),
    }

    def keep(ev: Dict[str, Any]) -> bool:
        if model and ev["model_name"] != model:
            return False
        if provider and ev["provider"] != provider:
            return False
        if task_type and ev["task_type"] != task_type:
            return False
        return True

    filtered = [ev for ev in normalized if keep(ev)]

    total = _empty_bucket()
    unknown_input = 0
    unknown_output = 0
    cached_reporting = 0
    by_provider: Dict[str, Dict[str, Any]] = {}
    by_model: Dict[str, Dict[str, Any]] = {}
    by_task: Dict[str, Dict[str, Any]] = {}
    model_provider: Dict[str, str] = {}

    for ev in filtered:
        _add(total, ev)
        if ev["cost_status"] == "unknown":
            unknown_input += ev["input_tokens"]
            unknown_output += ev["output_tokens"]
        if ev["cached_input_tokens"] is not None:
            cached_reporting += 1
        _add(by_provider.setdefault(ev["provider"], _empty_bucket()), ev)
        _add(by_model.setdefault(ev["model_name"], _empty_bucket()), ev)
        _add(by_task.setdefault(ev["task_type"], _empty_bucket()), ev)
        model_provider.setdefault(ev["model_name"], ev["provider"])

    models_breakdown = _sorted_breakdown(by_model)
    for item in models_breakdown:
        item["provider"] = model_provider.get(item["key"])

    return {
        "event_count": total["event_count"],
        "failed_count": total["failed_count"],
        "tokens": {
            "input": total["input_tokens"],
            "output": total["output_tokens"],
            "cached_input": total["cached_input_tokens"],
            "cached_input_reported_events": cached_reporting,
            "embedding": None,
            "embeddings_tracked": EMBEDDINGS_TRACKED,
        },
        "cost": {
            "known_usd": round(total["cost_known_usd"], 10),
            "known_cost_events": total["known_cost_events"],
            "free_events": total["free_events"],
            "unknown_cost_events": total["unknown_cost_events"],
            "unknown_cost_input_tokens": unknown_input,
            "unknown_cost_output_tokens": unknown_output,
            "is_complete": total["unknown_cost_events"] == 0,
        },
        "by_provider": _sorted_breakdown(by_provider),
        "by_model": models_breakdown,
        "by_task_type": _sorted_breakdown(by_task),
        "filter_options": options,
    }
