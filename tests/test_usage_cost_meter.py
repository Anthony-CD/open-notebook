"""Usage & cost meter: pricing, aggregation, callback, API.

No real model or network calls: the OpenRouter catalog, repo_query and
UsageEvent.save are all mocked.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from open_notebook.ai import pricing
from open_notebook.ai.pricing import (
    STATIC_PRICE_TABLE,
    PriceInfo,
    compute_cost,
    get_price_per_token,
    lookup_price,
)
from open_notebook.ai.provision import (
    UsageTrackingCallbackHandler,
    _extract_token_usage,
)
from open_notebook.domain.usage import UsageEvent, aggregate_events, classify_row


@pytest.fixture(autouse=True)
def _reset_openrouter_cache():
    pricing._price_cache = {}
    pricing._cache_loaded_at = 0.0
    pricing._cache_version = None
    yield
    pricing._price_cache = {}
    pricing._cache_loaded_at = 0.0
    pricing._cache_version = None


def _seed_openrouter(entries):
    """Pretend the catalog was just fetched (no network)."""
    import time

    pricing._price_cache = entries
    pricing._cache_loaded_at = time.monotonic()
    pricing._cache_version = "openrouter-catalog-test"


# ---------------------------------------------------------------- pricing
class TestPricing:
    @pytest.mark.asyncio
    async def test_openrouter_free_suffix_is_free(self):
        info = await lookup_price("openrouter", "nvidia/nemotron-3-super-120b-a12b:free")
        assert info.status == "free"
        assert info.is_free
        assert compute_cost(info, 1000, 1000) == 0.0

    @pytest.mark.asyncio
    async def test_catalog_zero_price_is_free(self):
        _seed_openrouter({"some/model": (0.0, 0.0, None)})
        info = await lookup_price("openrouter", "some/model")
        assert info.status == "free"

    @pytest.mark.asyncio
    async def test_catalog_price_is_known(self):
        _seed_openrouter({"deepseek/deepseek-v3.2": (2e-7, 4e-7, 2e-8)})
        info = await lookup_price("openrouter", "DeepSeek/DeepSeek-V3.2")
        assert info.status == "known"
        assert info.pricing_version == "openrouter-catalog-test"
        assert compute_cost(info, 1_000_000, 1_000_000) == pytest.approx(0.6)

    @pytest.mark.asyncio
    async def test_openrouter_model_missing_from_catalog_is_unknown(self):
        _seed_openrouter({"other/model": (1e-6, 1e-6, None)})
        info = await lookup_price("openrouter", "not/in-catalog")
        assert info.status == "unknown"
        assert compute_cost(info, 100, 100) is None

    @pytest.mark.asyncio
    async def test_unknown_provider_model_is_none_not_zero(self):
        info = await lookup_price("openai_compatible", "mystery-model")
        assert info.status == "unknown"
        assert compute_cost(info, 2_029_246, 25_579) is None

    @pytest.mark.asyncio
    async def test_nvidia_free_endpoint_is_free(self):
        info = await lookup_price(
            "openai_compatible",
            "nvidia/nemotron-3-ultra-550b-a55b",
            base_url="https://integrate.api.nvidia.com/v1",
        )
        assert info.status == "free"

    @pytest.mark.asyncio
    async def test_local_provider_is_free(self):
        assert (await lookup_price("ollama", "llama3")).status == "free"

    @pytest.mark.asyncio
    async def test_static_table_is_versioned(self):
        info = await lookup_price("groq", "llama-3.3-70b-versatile")
        assert info.status == "known"
        assert info.pricing_version == STATIC_PRICE_TABLE["version"]
        assert STATIC_PRICE_TABLE["effective_date"]
        # $0.59 in / $0.79 out per 1M
        assert compute_cost(info, 1_000_000, 1_000_000) == pytest.approx(1.38)

    @pytest.mark.asyncio
    async def test_google_models_prefix_is_stripped(self):
        info = await lookup_price("google", "models/gemini-2.0-flash")
        assert info.status == "known"

    def test_cached_input_uses_cache_price_when_known(self):
        info = PriceInfo(
            status="known",
            input_per_token=1e-6,
            output_per_token=2e-6,
            cached_input_per_token=1e-7,
            pricing_version="v",
        )
        # 1000 input of which 400 cached; 100 output
        cost = compute_cost(info, 1000, 100, cached_input_tokens=400)
        assert cost == pytest.approx(600 * 1e-6 + 400 * 1e-7 + 100 * 2e-6)

    def test_cached_input_without_cache_price_bills_full_input(self):
        info = PriceInfo(
            status="known", input_per_token=1e-6, output_per_token=0.0
        )
        assert compute_cost(info, 1000, 0, cached_input_tokens=400) == pytest.approx(1e-3)

    def test_rounding_removes_float_noise(self):
        info = PriceInfo(status="known", input_per_token=1e-7, output_per_token=3e-7)
        cost = compute_cost(info, 3, 3)
        assert cost == round(cost, 10)
        assert cost == pytest.approx(1.2e-6)

    @pytest.mark.asyncio
    async def test_get_price_per_token_backcompat_zero_for_unknown_and_free(self):
        assert await get_price_per_token("openai_compatible", "x") == (0.0, 0.0)
        assert await get_price_per_token("openrouter", "a/b:free") == (0.0, 0.0)
        ip, op = await get_price_per_token("groq", "llama-3.1-8b-instant")
        assert ip > 0 and op > 0

    @pytest.mark.asyncio
    async def test_lookup_never_raises(self):
        with patch.object(
            pricing, "_refresh_openrouter_pricing", AsyncMock(side_effect=RuntimeError)
        ):
            info = await lookup_price("openrouter", "a/b")
        assert info.status == "unknown"


# ------------------------------------------------------------ aggregation
def _row(**kw):
    base = {
        "provider": "openrouter",
        "model_name": "m",
        "task_type": "chat",
        "input_tokens": 0,
        "output_tokens": 0,
        "estimated_cost_usd": None,
        "cost_status": None,
        "failed": False,
    }
    base.update(kw)
    return base


class TestAggregation:
    def test_reported_bug_shape_is_unknown_not_zero(self):
        """Legacy rows: tokens recorded with cost 0 for a non-priced model must
        not become "$0 / no usage"."""
        rows = [
            _row(
                provider="openai_compatible",
                model_name="nvidia/nemotron-x",
                input_tokens=2_029_246,
                output_tokens=25_579,
                estimated_cost_usd=0.0,
            )
        ]
        s = aggregate_events(rows)
        assert s["event_count"] == 1
        assert s["tokens"]["input"] == 2_029_246
        assert s["cost"]["unknown_cost_events"] == 1
        assert s["cost"]["is_complete"] is False
        assert s["cost"]["unknown_cost_input_tokens"] == 2_029_246

    def test_mixed_known_unknown_free(self):
        rows = [
            _row(model_name="paid", input_tokens=100, output_tokens=10,
                 estimated_cost_usd=0.5, cost_status="known", task_type="chat"),
            _row(model_name="a/b:free", input_tokens=50, output_tokens=5,
                 estimated_cost_usd=0.0, cost_status="free", task_type="transformation"),
            _row(provider="openai_compatible", model_name="mystery", input_tokens=7,
                 output_tokens=3, cost_status="unknown", task_type="chat"),
            # legacy openrouter :free row, no cost_status
            _row(model_name="x/y:free", input_tokens=1, output_tokens=1,
                 estimated_cost_usd=0.0),
            # legacy priced row
            _row(model_name="paid", input_tokens=1, output_tokens=1,
                 estimated_cost_usd=0.25),
        ]
        s = aggregate_events(rows)
        assert s["event_count"] == 5
        assert s["cost"]["known_usd"] == pytest.approx(0.75)
        assert s["cost"]["free_events"] == 2
        assert s["cost"]["unknown_cost_events"] == 1
        assert s["cost"]["known_cost_events"] == 2
        assert s["tokens"]["input"] == 159
        chat = next(i for i in s["by_task_type"] if i["key"] == "chat")
        assert chat["event_count"] == 4
        assert chat["unknown_cost_events"] == 1
        assert chat["cost_known_usd"] == pytest.approx(0.75)
        paid = next(i for i in s["by_model"] if i["key"] == "paid")
        assert paid["provider"] == "openrouter"
        assert s["tokens"]["embeddings_tracked"] is False
        assert s["tokens"]["embedding"] is None

    def test_filters_and_options(self):
        rows = [
            _row(model_name="a", task_type="chat", input_tokens=1, cost_status="unknown"),
            _row(model_name="b", task_type="podcast", provider="groq", input_tokens=2,
                 cost_status="unknown"),
        ]
        s = aggregate_events(rows, model="b")
        assert s["event_count"] == 1
        assert s["tokens"]["input"] == 2
        assert s["filter_options"]["models"] == ["a", "b"]
        assert s["filter_options"]["task_types"] == ["chat", "podcast"]
        assert aggregate_events(rows, task_type="chat")["event_count"] == 1
        assert aggregate_events(rows, provider="groq")["event_count"] == 1
        assert aggregate_events(rows, provider="nope")["event_count"] == 0

    def test_empty(self):
        s = aggregate_events([])
        assert s["event_count"] == 0
        assert s["cost"]["known_usd"] == 0.0
        assert s["cost"]["is_complete"] is True

    def test_unknown_row_repriced_from_static_table(self):
        ev = classify_row(_row(provider="groq", model_name="llama-3.1-8b-instant",
                               input_tokens=1_000_000, output_tokens=0,
                               estimated_cost_usd=0.0))
        assert ev["cost_status"] == "known"
        assert ev["cost_usd"] == pytest.approx(0.05)

    def test_cached_tokens_summed(self):
        rows = [
            _row(input_tokens=10, cached_input_tokens=4, cost_status="unknown"),
            _row(input_tokens=10, cost_status="unknown"),
        ]
        s = aggregate_events(rows)
        assert s["tokens"]["cached_input"] == 4
        assert s["tokens"]["cached_input_reported_events"] == 1

    def test_failed_counted(self):
        rows = [_row(failed=True, cost_status="known", estimated_cost_usd=0.0)]
        s = aggregate_events(rows)
        assert s["failed_count"] == 1
        assert s["cost"]["unknown_cost_events"] == 0


class TestBudget:
    @pytest.mark.asyncio
    async def test_current_spend_counts_known_only(self):
        rows = [
            _row(estimated_cost_usd=1.5, cost_status="known", input_tokens=1),
            _row(provider="openai_compatible", model_name="x", input_tokens=5,
                 cost_status="unknown"),
        ]
        with patch("open_notebook.domain.usage.repo_query", AsyncMock(return_value=rows)) as q:
            assert await UsageEvent.get_current_spend_usd() == pytest.approx(1.5)
        assert "created >= $since" in q.await_args.args[0]

    @pytest.mark.asyncio
    async def test_is_within_budget_fails_closed_on_db_error(self):
        with patch(
            "open_notebook.domain.usage.repo_query",
            AsyncMock(side_effect=RuntimeError("db down")),
        ):
            assert await UsageEvent.is_within_budget() is False


# --------------------------------------------------------------- callback
def _result_with_usage(inp, out, cache_read=None):
    meta = {"input_tokens": inp, "output_tokens": out, "total_tokens": inp + out}
    if cache_read is not None:
        meta["input_token_details"] = {"cache_read": cache_read}
    msg = AIMessage(content="hi", usage_metadata=meta)
    return LLMResult(generations=[[ChatGeneration(message=msg)]])


class TestCallback:
    def test_extracts_cache_read(self):
        assert _extract_token_usage(_result_with_usage(100, 20, 30)) == (100, 20, 30)
        assert _extract_token_usage(_result_with_usage(100, 20)) == (100, 20, None)
        assert _extract_token_usage(None) == (0, 0, None)

    def test_extracts_openai_style_cached(self):
        r = LLMResult(
            generations=[[]],
            llm_output={"token_usage": {"prompt_tokens": 9, "completion_tokens": 2,
                                        "prompt_tokens_details": {"cached_tokens": 4}}},
        )
        assert _extract_token_usage(r) == (9, 2, 4)

    @pytest.mark.asyncio
    async def test_unknown_price_saved_as_none(self):
        handler = UsageTrackingCallbackHandler("openai_compatible", "mystery", "chat")
        saved = []

        async def capture(self):
            saved.append(self)

        with patch.object(UsageEvent, "save", capture):
            await handler.on_llm_end(_result_with_usage(1000, 10))
        assert len(saved) == 1
        ev = saved[0]
        assert ev.estimated_cost_usd is None
        assert ev.cost_status == "unknown"
        assert ev.failed is False
        # None is dropped from the payload so the DB stores NONE, not 0
        assert "estimated_cost_usd" not in ev._prepare_save_data()

    @pytest.mark.asyncio
    async def test_free_model_saved_as_zero_free(self):
        handler = UsageTrackingCallbackHandler("openrouter", "a/b:free", "chat")
        saved = []

        async def capture(self):
            saved.append(self)

        with patch.object(UsageEvent, "save", capture):
            await handler.on_llm_end(_result_with_usage(10, 10, 5))
        ev = saved[0]
        assert ev.cost_status == "free"
        assert ev.estimated_cost_usd == 0.0
        assert ev.cached_input_tokens == 5
        assert ev.pricing_version

    @pytest.mark.asyncio
    async def test_failed_response_recorded_without_raising(self):
        handler = UsageTrackingCallbackHandler("groq", "llama-3.1-8b-instant", "chat")
        saved = []

        async def capture(self):
            saved.append(self)

        with patch.object(UsageEvent, "save", capture):
            await handler.on_llm_error(RuntimeError("429 rate limited"))
        ev = saved[0]
        assert ev.failed is True
        assert ev.error_type == "RuntimeError"
        assert ev.input_tokens == 0 and ev.output_tokens == 0

    @pytest.mark.asyncio
    async def test_failed_with_partial_tokens_is_priced(self):
        handler = UsageTrackingCallbackHandler("groq", "llama-3.1-8b-instant", "chat")
        saved = []

        async def capture(self):
            saved.append(self)

        with patch.object(UsageEvent, "save", capture):
            await handler.on_llm_error(
                TimeoutError(), response=_result_with_usage(1_000_000, 0)
            )
        ev = saved[0]
        assert ev.failed is True
        assert ev.cost_status == "known"
        assert ev.estimated_cost_usd == pytest.approx(0.05)

    @pytest.mark.asyncio
    async def test_success_without_usage_is_unknown(self):
        handler = UsageTrackingCallbackHandler("groq", "llama-3.1-8b-instant", "chat")
        saved = []

        async def capture(self):
            saved.append(self)

        with patch.object(UsageEvent, "save", capture):
            await handler.on_llm_end(LLMResult(generations=[[]]))
        assert saved[0].cost_status == "unknown"
        assert saved[0].estimated_cost_usd is None

    @pytest.mark.asyncio
    async def test_recording_never_raises(self):
        handler = UsageTrackingCallbackHandler("groq", "x", "chat")
        with patch.object(UsageEvent, "save", AsyncMock(side_effect=RuntimeError("db"))):
            await handler.on_llm_end(_result_with_usage(1, 1))
            await handler.on_llm_error(ValueError("boom"))


# ----------------------------------------------------------- service/API
class TestService:
    def test_resolve_range_presets(self):
        from api.usage_service import resolve_range

        now = datetime(2026, 10, 3, tzinfo=timezone.utc)
        assert resolve_range("7d", None, None, now) == ("7d", now - timedelta(days=7), None)
        assert resolve_range("month", None, None, now)[0] == "30d"
        assert resolve_range("year", None, None, now)[0] == "365d"
        assert resolve_range("all", None, None, now) == ("all", None, None)
        preset, since, until = resolve_range("7d", "2026-10-01T10:00:00Z", None, now)
        assert preset == "custom"
        assert since == datetime(2026, 10, 1, 10, tzinfo=timezone.utc)

    def test_resolve_range_rejects_bad_input(self):
        from api.usage_service import resolve_range
        from open_notebook.exceptions import InvalidInputError

        with pytest.raises(InvalidInputError):
            resolve_range("bogus", None, None)
        with pytest.raises(InvalidInputError):
            resolve_range(None, "not-a-date", None)
        with pytest.raises(InvalidInputError):
            resolve_range(None, "2026-10-02T00:00:00Z", "2026-10-01T00:00:00Z")

    def test_endpoint_shape(self):
        from api.main import app

        rows = [
            _row(provider="openai_compatible", model_name="mystery", input_tokens=10,
                 output_tokens=2, estimated_cost_usd=0.0),
            _row(model_name="paid", input_tokens=5, output_tokens=5,
                 estimated_cost_usd=0.01, cost_status="known"),
        ]
        with patch(
            "open_notebook.domain.usage.repo_query", AsyncMock(return_value=rows)
        ) as q:
            client = TestClient(app)
            resp = client.get(
                "/api/usage/summary",
                params={"from": "2026-10-01T00:00:00Z", "task_type": "chat"},
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["event_count"] == 2
        assert body["period"]["preset"] == "custom"
        assert body["period"]["from"].startswith("2026-10-01")
        assert body["cost"]["unknown_cost_events"] == 1
        assert body["cost"]["known_usd"] == pytest.approx(0.01)
        assert body["generated_at"]
        assert body["applied_filters"]["task_type"] == "chat"
        assert "since" in q.await_args.args[1]

    def test_endpoint_rejects_bad_period(self):
        from api.main import app

        client = TestClient(app)
        with patch("open_notebook.domain.usage.repo_query", AsyncMock(return_value=[])):
            resp = client.get("/api/usage/summary", params={"period": "bogus"})
        assert resp.status_code == 400


def test_migration_28_registered():
    from open_notebook.database.async_migrate import AsyncMigrationManager

    manager = AsyncMigrationManager()
    assert len(manager.up_migrations) == len(manager.down_migrations)
    up = manager.up_migrations[27].sql
    down = manager.down_migrations[27].sql
    assert "cost_status" in up and "option<float>" in up
    assert "REMOVE FIELD IF EXISTS cost_status" in down
    assert Path("open_notebook/database/migrations/28_down.surrealql").exists()
