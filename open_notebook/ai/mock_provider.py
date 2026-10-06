"""Mock LLM provider for local QA without spending real tokens.

Lets a developer register a `Model` with `provider="mock"` (Settings ->
Models -> Add Model, no credential required) and point default/transformation
models at it to click through chat, transformations, podcasts, etc. without
calling a real paid provider.

Hard-gated: `ModelManager.get_model()` (open_notebook/ai/models.py) only
instantiates this when `mock_provider_enabled()` is true, which requires the
`OPEN_NOTEBOOK_ENABLE_MOCK_PROVIDER` env var to be explicitly set. A `Model`
row with provider="mock" existing in the database (e.g. left over from a QA
session) is harmless on a deployment that never sets the env var - it just
fails closed with a clear ConfigurationError instead of silently doing
nothing or, worse, falling through to a real provider. See
docs/5-CONFIGURATION or CONFIGURATION.md for how to enable it.

Cost: `open_notebook/ai/pricing.py` treats provider "mock" as a local-free
provider (same bucket as ollama/omlx) - it never calls a real API, so $0 is
the honest, known price, not an unknown one.
"""

import os
from typing import Any, Dict, List, Optional

from esperanto.common_types import Model as EsperantoModelInfo
from esperanto.providers.llm.base import LanguageModel
from langchain_core.callbacks.manager import (
    AsyncCallbackManagerForLLMRun,
    CallbackManagerForLLMRun,
)
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult

MOCK_PROVIDER_ENV_VAR = "OPEN_NOTEBOOK_ENABLE_MOCK_PROVIDER"
MOCK_PROVIDER_NAME = "mock"

DEFAULT_MOCK_RESPONSE = (
    "[Open Notebook mock provider] This is a canned response - no real model "
    "was called and no tokens were spent. Disable OPEN_NOTEBOOK_ENABLE_MOCK_PROVIDER "
    "and point your models at a real provider for actual output."
)


def mock_provider_enabled() -> bool:
    """Whether the mock provider may be instantiated.

    Off by default. Never flip this on in a deployment real users touch -
    it silently replaces real model output with a canned string.
    """
    return os.getenv(MOCK_PROVIDER_ENV_VAR, "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


class _MockChatModel(BaseChatModel):
    """A `BaseChatModel` that returns a canned response with fake token usage.

    Deterministic token counts (not a real tokenizer) so usage tracking and
    the Cost Meter still exercise their full code path during manual QA
    without needing a real provider call.
    """

    response_text: str = DEFAULT_MOCK_RESPONSE
    model_name: str = "mock"

    @property
    def _llm_type(self) -> str:
        return "open-notebook-mock-chat"

    def _usage_metadata(self, messages: List[BaseMessage]) -> Dict[str, int]:
        input_tokens = sum(len(str(getattr(m, "content", ""))) for m in messages) // 4
        output_tokens = len(self.response_text) // 4
        return {
            "input_tokens": max(input_tokens, 1),
            "output_tokens": max(output_tokens, 1),
            "total_tokens": max(input_tokens, 1) + max(output_tokens, 1),
        }

    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        message = AIMessage(
            content=self.response_text,
            usage_metadata=self._usage_metadata(messages),  # type: ignore[arg-type]
        )
        return ChatResult(generations=[ChatGeneration(message=message)])

    async def _agenerate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[AsyncCallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        return self._generate(messages, stop=stop, **kwargs)


class MockLanguageModel(LanguageModel):
    """Esperanto-shaped wrapper so `provision_langchain_model()`'s
    `isinstance(model, LanguageModel)` check and `.to_langchain()` call work
    unchanged for the mock provider - it is never special-cased past
    `ModelManager.get_model()`.
    """

    @property
    def provider(self) -> str:
        return MOCK_PROVIDER_NAME

    def _get_default_model(self) -> str:
        return "mock-chat"

    def _get_models(self) -> List[EsperantoModelInfo]:
        return [
            EsperantoModelInfo(id="mock-chat", owned_by=MOCK_PROVIDER_NAME, context_window=None)
        ]

    def chat_complete(self, messages: List[Dict[str, Any]], **kwargs: Any) -> Any:
        raise NotImplementedError(
            "MockLanguageModel only supports the to_langchain() path used by "
            "provision_langchain_model(); it does not implement Esperanto's "
            "native chat_complete()."
        )

    async def achat_complete(self, messages: List[Dict[str, Any]], **kwargs: Any) -> Any:
        raise NotImplementedError(
            "MockLanguageModel only supports the to_langchain() path used by "
            "provision_langchain_model(); it does not implement Esperanto's "
            "native achat_complete()."
        )

    def to_langchain(self) -> BaseChatModel:
        return _MockChatModel(model_name=self.model_name or self._get_default_model())


def build_mock_language_model(model_name: Optional[str]) -> MockLanguageModel:
    """Construct the mock LanguageModel for a given `Model.name`.

    Caller (`ModelManager.get_model()`) must check `mock_provider_enabled()`
    first - this function does not re-check it, so it stays trivially
    testable without the env var set.
    """
    return MockLanguageModel(model_name=model_name or "mock-chat")
