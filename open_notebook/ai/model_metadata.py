"""Honest, best-effort model metadata (context window, capabilities).

Scope note (P2 "Modelos, costo y confianza"): the only genuinely known
metadata in this codebase comes from two sources:

1. **OpenRouter's live catalog** (`/api/v1/models`) - the same endpoint
   `open_notebook/ai/pricing.py` uses for pricing. Each entry carries
   `context_length`, `architecture.input_modalities` (vision when `"image"`
   is one of them) and `supported_parameters` (tool-use when `"tools"` is
   one of them). This is real, provider-reported data.
2. **A user-configured Ollama/oMLX credential's `num_ctx`** - the context
   window the user explicitly set for that local runtime (see
   `CreateCredentialRequest.num_ctx` / `UpdateCredentialRequest.num_ctx`).

Every other provider (direct Anthropic/OpenAI/Google/Groq/... API calls) is
reported as `context_window=None`, `capabilities=[]`,
`capabilities_known=False`. We deliberately do NOT hardcode "Claude Opus has
a 200k window and supports vision" tables here: those would silently go
stale and can't be told apart from genuine unknowns by a caller. There is
also no "relative speed" field anywhere in this module - no provider or
library in this codebase reports latency/throughput, so inventing a
fast/standard/slow label would be a fabrication, not a metric. If that ever
becomes trackable (e.g. from real call latencies in UsageEvent), it belongs
there, not here.
"""

import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import httpx
from loguru import logger

from open_notebook.ai.provider_registry import PROVIDERS

# Capabilities we are willing to report. Anything else a catalog might
# advertise (audio, document input, reasoning, ...) is left out on purpose -
# we only surface what's useful for the compatibility checks in
# open_notebook/ai/provision.py and the model selector today.
KNOWN_CAPABILITIES = ("vision", "tools")

_CACHE_TTL_SECONDS = 300


@dataclass(frozen=True)
class ModelMetadata:
    context_window: Optional[int] = None
    capabilities: Tuple[str, ...] = ()
    # False when we have no catalog to ask at all (e.g. a direct Anthropic/
    # OpenAI call) - the UI must render this as "not available", never as
    # "confirmed this model has no capabilities".
    capabilities_known: bool = False
    source: str = "unknown"


UNKNOWN_METADATA = ModelMetadata()

# model_id(lower) -> raw OpenRouter catalog entry fields we care about.
_metadata_cache: Dict[str, Dict[str, Any]] = {}
_cache_loaded_at: float = 0.0


async def _refresh_openrouter_metadata() -> None:
    global _metadata_cache, _cache_loaded_at

    url = PROVIDERS["openrouter"].openai_compat_discovery_url
    if not url:
        return

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, timeout=10.0)
            response.raise_for_status()
            data = response.json()
    except Exception as e:
        logger.warning(f"Failed to refresh OpenRouter model metadata cache: {e}")
        _cache_loaded_at = time.monotonic()
        return

    new_cache: Dict[str, Dict[str, Any]] = {}
    for model in data.get("data", []):
        model_id = model.get("id")
        if not model_id:
            continue
        new_cache[model_id.lower()] = {
            "context_length": model.get("context_length"),
            "architecture": model.get("architecture") or {},
            "supported_parameters": model.get("supported_parameters") or [],
        }

    _metadata_cache = new_cache
    _cache_loaded_at = time.monotonic()


def _capabilities_from_openrouter_entry(entry: Dict[str, Any]) -> List[str]:
    caps: List[str] = []
    architecture = entry.get("architecture") or {}
    input_modalities = architecture.get("input_modalities") or []
    if isinstance(input_modalities, list) and "image" in input_modalities:
        caps.append("vision")
    supported_params = entry.get("supported_parameters") or []
    if isinstance(supported_params, list) and "tools" in supported_params:
        caps.append("tools")
    return caps


async def get_model_metadata(
    provider: str,
    model_name: str,
    credential_num_ctx: Optional[int] = None,
) -> ModelMetadata:
    """Resolve honest metadata for one (provider, model). Never raises.

    `credential_num_ctx` lets a caller that already has the linked
    credential in hand (Ollama/oMLX's user-configured context window) pass
    it through without this module re-fetching the credential itself.
    """
    try:
        if not provider or not model_name:
            return UNKNOWN_METADATA

        provider_l = provider.lower()

        if provider_l in ("ollama", "omlx") and credential_num_ctx:
            return ModelMetadata(
                context_window=int(credential_num_ctx),
                capabilities=(),
                capabilities_known=False,
                source="credential-configured",
            )

        if provider_l == "openrouter":
            if (
                not _metadata_cache
                or (time.monotonic() - _cache_loaded_at) > _CACHE_TTL_SECONDS
            ):
                await _refresh_openrouter_metadata()
            entry = _metadata_cache.get(model_name.lower())
            if entry is not None:
                context_window = entry.get("context_length")
                return ModelMetadata(
                    context_window=int(context_window) if context_window else None,
                    capabilities=tuple(_capabilities_from_openrouter_entry(entry)),
                    capabilities_known=True,
                    source="openrouter-catalog",
                )

        return UNKNOWN_METADATA
    except Exception as e:
        logger.warning(f"Model metadata lookup failed for {provider}/{model_name}: {e}")
        return UNKNOWN_METADATA
