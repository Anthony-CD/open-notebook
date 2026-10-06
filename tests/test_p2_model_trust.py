"""Tests for P2 "Modelos, costo y confianza":

- Honest model metadata (context window / capabilities) from the OpenRouter
  catalog and from a user-configured Ollama/oMLX num_ctx - never fabricated
  for providers with no catalog.
- Context-window preflight check in provision_langchain_model() (item 2).
- Usage-event idempotency: a single handler instance never writes twice for
  the same completed/failed response, while two distinct attempts (e.g. a
  real retry across the fallback chain) each still get their own event
  (item 4).
- Configurable daily/monthly budgets and the pre-expensive-operation check
  (item 3).
- The dev-only mock provider, hard-gated behind its env var (item 5).

No real model or network calls: the OpenRouter catalog, repo_query and
UsageEvent.save are all mocked, same convention as test_usage_cost_meter.py.
"""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from open_notebook.ai import model_metadata
from open_notebook.ai.mock_provider import (
    MOCK_PROVIDER_ENV_VAR,
    MockLanguageModel,
    build_mock_language_model,
    mock_provider_enabled,
)
from open_notebook.ai.model_metadata import ModelMetadata, get_model_metadata
from open_notebook.ai.models import Model, ModelManager
from open_notebook.ai.provision import UsageTrackingCallbackHandler
from open_notebook.domain.usage import (
    check_operation_against_budget,
    get_budget_limits,
    get_budget_status,
)
from open_notebook.exceptions import ConfigurationError


@pytest.fixture(autouse=True)
def _reset_metadata_cache():
    model_metadata._metadata_cache = {}
    model_metadata._cache_loaded_at = 0.0
    yield
    model_metadata._metadata_cache = {}
    model_metadata._cache_loaded_at = 0.0


def _seed_openrouter_metadata(entries):
    model_metadata._metadata_cache = entries
    model_metadata._cache_loaded_at = time.monotonic()


# --------------------------------------------------------------- metadata
class TestModelMetadata:
    @pytest.mark.asyncio
    async def test_openrouter_vision_and_tools_detected(self):
        _seed_openrouter_metadata(
            {
                "vendor/vision-model": {
                    "context_length": 128000,
                    "architecture": {"input_modalities": ["text", "image"]},
                    "supported_parameters": ["tools", "temperature"],
                }
            }
        )
        meta = await get_model_metadata("openrouter", "vendor/vision-model")
        assert meta.context_window == 128000
        assert set(meta.capabilities) == {"vision", "tools"}
        assert meta.capabilities_known is True
        assert meta.source == "openrouter-catalog"

    @pytest.mark.asyncio
    async def test_openrouter_text_only_model_has_no_vision(self):
        _seed_openrouter_metadata(
            {
                "vendor/text-model": {
                    "context_length": 32000,
                    "architecture": {"input_modalities": ["text"]},
                    "supported_parameters": [],
                }
            }
        )
        meta = await get_model_metadata("openrouter", "vendor/text-model")
        assert meta.context_window == 32000
        assert meta.capabilities == ()
        assert meta.capabilities_known is True

    @pytest.mark.asyncio
    async def test_openrouter_model_missing_from_catalog_is_unknown(self):
        _seed_openrouter_metadata({"other/model": {"context_length": 1000}})
        meta = await get_model_metadata("openrouter", "not/in-catalog")
        assert meta.context_window is None
        assert meta.capabilities_known is False

    @pytest.mark.asyncio
    async def test_direct_provider_is_honestly_unknown(self):
        """Anthropic/OpenAI/etc direct calls have no catalog in this codebase -
        metadata must stay unknown, never fabricated."""
        meta = await get_model_metadata("anthropic", "claude-sonnet-5")
        assert meta.context_window is None
        assert meta.capabilities == ()
        assert meta.capabilities_known is False
        assert meta.source == "unknown"

    @pytest.mark.asyncio
    async def test_ollama_uses_credential_configured_num_ctx(self):
        meta = await get_model_metadata("ollama", "llama3", credential_num_ctx=16384)
        assert meta.context_window == 16384
        assert meta.source == "credential-configured"
        # A user-set num_ctx is a window size, not a verified capability list.
        assert meta.capabilities_known is False

    @pytest.mark.asyncio
    async def test_ollama_without_num_ctx_is_unknown(self):
        meta = await get_model_metadata("ollama", "llama3")
        assert meta.context_window is None

    @pytest.mark.asyncio
    async def test_never_raises_on_catalog_failure(self):
        with patch.object(
            model_metadata,
            "_refresh_openrouter_metadata",
            AsyncMock(side_effect=RuntimeError("network down")),
        ):
            meta = await get_model_metadata("openrouter", "a/b")
        assert meta == ModelMetadata()


# ------------------------------------------------------- context preflight
class TestContextWindowPreflight:
    @pytest.mark.asyncio
    async def test_explicit_model_oversized_content_raises_422(self):
        from open_notebook.ai.provision import provision_langchain_model

        _seed_openrouter_metadata(
            {"vendor/small-context": {"context_length": 8000, "architecture": {}, "supported_parameters": []}}
        )

        from esperanto import LanguageModel

        spec_fake = MagicMock(spec=LanguageModel)
        spec_fake.provider = "openrouter"
        spec_fake.get_model_name.return_value = "vendor/small-context"

        huge_content = "word " * 20000  # well over 8000 * 0.9 tokens

        with patch(
            "open_notebook.ai.provision.model_manager.get_model",
            new=AsyncMock(return_value=spec_fake),
        ):
            with pytest.raises(ConfigurationError):
                await provision_langchain_model(huge_content, "model:small", "chat")

    @pytest.mark.asyncio
    async def test_unknown_context_window_does_not_block(self):
        """No catalog data (e.g. a direct Anthropic model) -> never blocks,
        even for huge content - we don't fabricate a window to enforce."""
        from esperanto import LanguageModel

        from open_notebook.ai.provision import provision_langchain_model

        spec_fake = MagicMock(spec=LanguageModel)
        spec_fake.provider = "anthropic"
        spec_fake.get_model_name.return_value = "claude-sonnet-5"
        spec_fake.to_langchain.return_value = MagicMock()
        spec_fake.to_langchain.return_value.callbacks = None

        huge_content = "word " * 50000

        with (
            patch(
                "open_notebook.ai.provision.model_manager.get_model",
                new=AsyncMock(return_value=spec_fake),
            ),
            patch(
                "open_notebook.ai.provision.model_manager.get_fallback_chain",
                new=AsyncMock(return_value=[]),
            ),
        ):
            result = await provision_langchain_model(huge_content, "model:x", "chat")
        assert result is spec_fake.to_langchain.return_value


# ------------------------------------------------------------ idempotency
def _result_with_usage(inp, out):
    msg = AIMessage(
        content="hi",
        usage_metadata={"input_tokens": inp, "output_tokens": out, "total_tokens": inp + out},
    )
    return LLMResult(generations=[[ChatGeneration(message=msg)]])


class TestIdempotency:
    @pytest.mark.asyncio
    async def test_same_handler_fired_twice_writes_once(self):
        """Guards against an application-level double-write of the SAME
        completed response (e.g. a future bug firing on_llm_end twice)."""
        handler = UsageTrackingCallbackHandler("groq", "llama-3.1-8b-instant", "chat")
        with patch("open_notebook.domain.usage.UsageEvent.save", new=AsyncMock()) as save:
            await handler.on_llm_end(_result_with_usage(10, 5))
            await handler.on_llm_end(_result_with_usage(10, 5))
        assert save.await_count == 1

    @pytest.mark.asyncio
    async def test_end_then_error_on_same_handler_writes_once(self):
        handler = UsageTrackingCallbackHandler("groq", "llama-3.1-8b-instant", "chat")
        with patch("open_notebook.domain.usage.UsageEvent.save", new=AsyncMock()) as save:
            await handler.on_llm_end(_result_with_usage(10, 5))
            await handler.on_llm_error(RuntimeError("boom"))
        assert save.await_count == 1

    @pytest.mark.asyncio
    async def test_distinct_attempts_each_get_their_own_event(self):
        """A genuine retry (e.g. primary fails, fallback serves the request)
        is two real handler instances - each still records its own event;
        retries must never be under-counted."""
        primary_handler = UsageTrackingCallbackHandler("groq", "model-a", "chat")
        fallback_handler = UsageTrackingCallbackHandler("groq", "model-b", "chat")
        assert primary_handler.attempt_id != fallback_handler.attempt_id

        with patch("open_notebook.domain.usage.UsageEvent.save", new=AsyncMock()) as save:
            await primary_handler.on_llm_error(RuntimeError("rate limited"))
            await fallback_handler.on_llm_end(_result_with_usage(10, 5))
        assert save.await_count == 2

    @pytest.mark.asyncio
    async def test_attempt_id_is_stored_on_the_event(self):
        handler = UsageTrackingCallbackHandler("groq", "llama-3.1-8b-instant", "chat")
        with patch("open_notebook.domain.usage.UsageEvent") as MockEvent:
            MockEvent.return_value.save = AsyncMock()
            await handler.on_llm_end(_result_with_usage(10, 5))
        assert MockEvent.call_args.kwargs["attempt_id"] == handler.attempt_id


# ---------------------------------------------------------------- budgets
class _FakeSettings:
    def __init__(self, daily=None, monthly=None):
        self.daily_budget_usd = daily
        self.monthly_budget_usd = monthly


class TestBudgets:
    @pytest.mark.asyncio
    async def test_no_settings_falls_back_to_env_monthly_no_daily(self, monkeypatch):
        monkeypatch.setenv("STUDY_BUDGET_USD", "25")
        with patch(
            "open_notebook.domain.content_settings.ContentSettings.get_instance",
            new=AsyncMock(return_value=_FakeSettings()),
        ):
            limits = await get_budget_limits()
        assert limits == {"daily_usd": None, "monthly_usd": 25.0}

    @pytest.mark.asyncio
    async def test_configured_settings_override_env(self, monkeypatch):
        monkeypatch.setenv("STUDY_BUDGET_USD", "10")
        with patch(
            "open_notebook.domain.content_settings.ContentSettings.get_instance",
            new=AsyncMock(return_value=_FakeSettings(daily=2.5, monthly=50.0)),
        ):
            limits = await get_budget_limits()
        assert limits == {"daily_usd": 2.5, "monthly_usd": 50.0}

    @pytest.mark.asyncio
    async def test_budget_status_splits_daily_and_monthly_spend(self):
        with (
            patch(
                "open_notebook.domain.usage.get_budget_limits",
                new=AsyncMock(return_value={"daily_usd": 1.0, "monthly_usd": 10.0}),
            ),
            patch(
                "open_notebook.domain.usage._known_spend_since",
                new=AsyncMock(side_effect=[0.5, 3.0]),
            ),
        ):
            status = await get_budget_status()
        assert status["daily"] == {
            "limit_usd": 1.0,
            "spent_usd": 0.5,
            "remaining_usd": 0.5,
            "is_over": False,
        }
        assert status["monthly"]["spent_usd"] == 3.0
        assert status["monthly"]["remaining_usd"] == 7.0

    @pytest.mark.asyncio
    async def test_check_operation_flags_would_exceed(self):
        with (
            patch(
                "open_notebook.domain.usage.get_budget_limits",
                new=AsyncMock(return_value={"daily_usd": 1.0, "monthly_usd": 10.0}),
            ),
            patch(
                "open_notebook.domain.usage._known_spend_since",
                new=AsyncMock(side_effect=[0.9, 9.5]),
            ),
        ):
            result = await check_operation_against_budget(0.6)
        assert result["would_exceed_daily"] is True
        assert result["would_exceed_monthly"] is True
        assert result["estimated_cost_usd"] == 0.6

    @pytest.mark.asyncio
    async def test_check_operation_never_blocks_only_reports(self):
        """No daily/monthly limit configured -> never flags would_exceed,
        regardless of estimate (no silent block)."""
        with (
            patch(
                "open_notebook.domain.usage.get_budget_limits",
                new=AsyncMock(return_value={"daily_usd": None, "monthly_usd": None}),
            ),
            patch(
                "open_notebook.domain.usage._known_spend_since",
                new=AsyncMock(return_value=0.0),
            ),
        ):
            result = await check_operation_against_budget(1_000_000.0)
        assert result["would_exceed_daily"] is False
        assert result["would_exceed_monthly"] is False


# ----------------------------------------------------------- mock provider
class TestMockProvider:
    def test_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv(MOCK_PROVIDER_ENV_VAR, raising=False)
        assert mock_provider_enabled() is False

    def test_enabled_when_env_var_truthy(self, monkeypatch):
        monkeypatch.setenv(MOCK_PROVIDER_ENV_VAR, "true")
        assert mock_provider_enabled() is True

    @pytest.mark.asyncio
    async def test_get_model_rejects_mock_when_disabled(self, monkeypatch):
        monkeypatch.delenv(MOCK_PROVIDER_ENV_VAR, raising=False)
        manager = ModelManager()
        fake_model = Model(id="model:mock1", name="mock-chat", provider="mock", type="language")
        with patch(
            "open_notebook.ai.models.Model.get", new=AsyncMock(return_value=fake_model)
        ):
            with pytest.raises(ConfigurationError):
                await manager.get_model("model:mock1")

    @pytest.mark.asyncio
    async def test_get_model_returns_mock_when_enabled(self, monkeypatch):
        monkeypatch.setenv(MOCK_PROVIDER_ENV_VAR, "true")
        manager = ModelManager()
        fake_model = Model(id="model:mock1", name="mock-chat", provider="mock", type="language")
        with patch(
            "open_notebook.ai.models.Model.get", new=AsyncMock(return_value=fake_model)
        ):
            result = await manager.get_model("model:mock1")
        assert isinstance(result, MockLanguageModel)
        assert result.provider == "mock"

    @pytest.mark.asyncio
    async def test_mock_chat_model_returns_canned_response_with_usage(self):
        lc_model = build_mock_language_model("mock-chat").to_langchain()
        from langchain_core.messages import HumanMessage

        result = await lc_model.ainvoke([HumanMessage(content="hello")])
        assert result.content
        assert result.usage_metadata["input_tokens"] > 0
        assert result.usage_metadata["output_tokens"] > 0

    def test_mock_provider_is_free(self):
        from open_notebook.ai.pricing import classify_free

        assert classify_free("mock", "mock-chat") is True
