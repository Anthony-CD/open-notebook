"""Pricing lookup for LLM usage-cost tracking.

Three sources, tried in this order by `lookup_price()`:

1. **Explicitly free** models: OpenRouter's `:free` suffix (and the
   `-free` / `/free` tags some aggregators use), local runtimes (Ollama,
   oMLX) and NVIDIA's hosted free endpoints (integrate.api.nvidia.com,
   reached through the generic `openai_compatible` provider). These cost 0
   and are reported as `cost_status="free"`.
2. **OpenRouter's live catalog** (`/api/v1/models`, the same endpoint
   model discovery uses): USD per token for every proxied model. A catalog
   price of 0/0 is treated as free.
3. **A static, versioned price table** (`STATIC_PRICE_TABLE`) for direct
   provider calls. Only models whose list price we are confident about are
   listed; everything else is left out on purpose.

Anything not resolved by these is **unknown**: `lookup_price()` returns a
`PriceInfo` with `status="unknown"` and the computed cost is `None` - never
a fake 0. Pricing is a best-effort enrichment; nothing here may raise or
block an LLM call.

`get_price_per_token()` is kept for backward compatibility (budget-aware
fallback selection in open_notebook/ai/models.py uses it to decide whether
a fallback model is paid). It still returns (0.0, 0.0) for unknown/free.
"""

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Literal, Optional, Tuple
from urllib.parse import urlparse

import httpx
from loguru import logger

from open_notebook.ai.provider_registry import PROVIDERS

CostStatus = Literal["known", "free", "unknown"]

# Refresh at most every few minutes - a simple module-level cache is enough
# here; this isn't hit on a hot path (once per LLM call at most).
_CACHE_TTL_SECONDS = 300

_PER_MILLION = 1_000_000.0

# ---------------------------------------------------------------------------
# Static, versioned price table (USD per 1M tokens, list prices).
# Bump `version` + `effective_date` whenever any price changes so stored
# usage events can be traced back to the table that priced them.
# Keys are (provider, model_name) lowercased. `cached_input` is the price of
# cache-read input tokens where the provider publishes one.
# ---------------------------------------------------------------------------
STATIC_PRICE_TABLE: Dict[str, Any] = {
    "version": "static-2026-10-03",
    "effective_date": "2026-10-03",
    "models": {
        # Anthropic first-party API
        ("anthropic", "claude-opus-5-5"): {"input": 4.0, "output": 20.0, "cached_input": 0.20},
        ("anthropic", "claude-opus-5"): {"input": 5.0, "output": 25.0, "cached_input": 0.50},
        ("anthropic", "claude-opus-4-8"): {"input": 5.0, "output": 25.0, "cached_input": 0.50},
        ("anthropic", "claude-opus-4-7"): {"input": 5.0, "output": 25.0, "cached_input": 0.50},
        ("anthropic", "claude-opus-4-6"): {"input": 5.0, "output": 25.0, "cached_input": 0.50},
        ("anthropic", "claude-sonnet-5-5"): {"input": 2.0, "output": 10.0, "cached_input": 0.20},
        ("anthropic", "claude-sonnet-5"): {"input": 2.0, "output": 10.0, "cached_input": 0.20},
        ("anthropic", "claude-sonnet-4-6"): {"input": 3.0, "output": 15.0, "cached_input": 0.30},
        ("anthropic", "claude-haiku-4-5"): {"input": 1.0, "output": 5.0, "cached_input": 0.10},
        # OpenAI
        ("openai", "gpt-4o"): {"input": 2.50, "output": 10.0, "cached_input": 1.25},
        ("openai", "gpt-4o-mini"): {"input": 0.15, "output": 0.60, "cached_input": 0.075},
        # Google AI (Gemini API, paid-tier list price; free-tier usage is
        # still estimated at list price because the tier isn't observable)
        ("google", "gemini-2.0-flash"): {"input": 0.10, "output": 0.40},
        ("google", "gemini-2.5-flash"): {"input": 0.30, "output": 2.50},
        ("google", "gemini-embedding-001"): {"input": 0.15, "output": 0.0},
        # Groq
        ("groq", "llama-3.3-70b-versatile"): {"input": 0.59, "output": 0.79},
        ("groq", "llama-3.1-8b-instant"): {"input": 0.05, "output": 0.08},
    },
}

# Providers that run on the user's own hardware - no per-token billing.
_LOCAL_FREE_PROVIDERS = {"ollama", "omlx"}
# Hosts of explicitly free hosted endpoints (NVIDIA Developer Program).
_FREE_ENDPOINT_HOSTS = {"integrate.api.nvidia.com"}


@dataclass(frozen=True)
class PriceInfo:
    """Resolved price for one (provider, model). Prices are USD per token."""

    status: CostStatus
    input_per_token: Optional[float] = None
    output_per_token: Optional[float] = None
    cached_input_per_token: Optional[float] = None
    pricing_version: Optional[str] = None

    @property
    def is_free(self) -> bool:
        return self.status == "free"


UNKNOWN_PRICE = PriceInfo(status="unknown")

# model_id(lower) -> (prompt, completion, cache_read or None) USD per token
_price_cache: Dict[str, Tuple[Optional[float], Optional[float], Optional[float]]] = {}
_cache_loaded_at: float = 0.0
_cache_version: Optional[str] = None


def _parse_price(value: Any) -> Optional[float]:
    """Parse a catalog price; None when missing or malformed (never a fake 0)."""
    if value is None or value == "":
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    # OpenRouter uses "-1" for "variable / not applicable" (e.g. openrouter/auto).
    if parsed < 0:
        return None
    return parsed


async def _refresh_openrouter_pricing() -> None:
    global _price_cache, _cache_loaded_at, _cache_version

    url = PROVIDERS["openrouter"].openai_compat_discovery_url
    if not url:
        return

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, timeout=10.0)
            response.raise_for_status()
            data = response.json()
    except Exception as e:
        logger.warning(f"Failed to refresh OpenRouter pricing cache: {e}")
        # Keep serving the previous (possibly empty) cache rather than
        # hammering the API again on the very next call.
        _cache_loaded_at = time.monotonic()
        return

    new_cache: Dict[str, Tuple[Optional[float], Optional[float], Optional[float]]] = {}
    for model in data.get("data", []):
        model_id = model.get("id")
        if not model_id:
            continue
        pricing = model.get("pricing") or {}
        new_cache[model_id.lower()] = (
            _parse_price(pricing.get("prompt")),
            _parse_price(pricing.get("completion")),
            _parse_price(pricing.get("input_cache_read")),
        )

    _price_cache = new_cache
    _cache_loaded_at = time.monotonic()
    _cache_version = "openrouter-catalog-" + datetime.now(timezone.utc).strftime(
        "%Y-%m-%d"
    )


def _is_explicitly_free_name(model_name: str) -> bool:
    name = model_name.lower()
    return name.endswith(":free") or name.endswith("-free") or name.endswith("/free")


def _host(base_url: Optional[str]) -> str:
    if not base_url:
        return ""
    try:
        return (urlparse(base_url).hostname or "").lower()
    except Exception:
        return ""


def free_rule_version() -> str:
    return f"free-rule-{STATIC_PRICE_TABLE['version']}"


def classify_free(
    provider: str, model_name: str, base_url: Optional[str] = None
) -> bool:
    """True when the model is *explicitly* free (no catalog needed)."""
    provider_l = (provider or "").lower()
    if provider_l in _LOCAL_FREE_PROVIDERS:
        return True
    if model_name and _is_explicitly_free_name(model_name):
        return True
    if _host(base_url) in _FREE_ENDPOINT_HOSTS:
        return True
    return False


def static_price(provider: str, model_name: str) -> Optional[PriceInfo]:
    """Look a model up in the static versioned table (None if absent)."""
    if not provider or not model_name:
        return None
    name = model_name.lower()
    if name.startswith("models/"):  # Google SDKs sometimes prefix this
        name = name[len("models/"):]
    entry = STATIC_PRICE_TABLE["models"].get((provider.lower(), name))
    if not entry:
        return None
    cached = entry.get("cached_input")
    return PriceInfo(
        status="known",
        input_per_token=entry["input"] / _PER_MILLION,
        output_per_token=entry["output"] / _PER_MILLION,
        cached_input_per_token=(cached / _PER_MILLION) if cached is not None else None,
        pricing_version=STATIC_PRICE_TABLE["version"],
    )


async def lookup_price(
    provider: str, model_name: str, base_url: Optional[str] = None
) -> PriceInfo:
    """Resolve the price of a model. Never raises; unknown -> UNKNOWN_PRICE."""
    try:
        if not provider or not model_name:
            return UNKNOWN_PRICE

        if classify_free(provider, model_name, base_url):
            return PriceInfo(
                status="free",
                input_per_token=0.0,
                output_per_token=0.0,
                cached_input_per_token=0.0,
                pricing_version=free_rule_version(),
            )

        if provider.lower() == "openrouter":
            if (
                not _price_cache
                or (time.monotonic() - _cache_loaded_at) > _CACHE_TTL_SECONDS
            ):
                await _refresh_openrouter_pricing()
            entry = _price_cache.get(model_name.lower())
            if entry is not None:
                prompt, completion, cache_read = entry
                if prompt is not None and completion is not None:
                    version = _cache_version or "openrouter-catalog"
                    if prompt == 0 and completion == 0:
                        return PriceInfo(
                            status="free",
                            input_per_token=0.0,
                            output_per_token=0.0,
                            cached_input_per_token=0.0,
                            pricing_version=version,
                        )
                    return PriceInfo(
                        status="known",
                        input_per_token=prompt,
                        output_per_token=completion,
                        cached_input_per_token=cache_read,
                        pricing_version=version,
                    )

        return static_price(provider, model_name) or UNKNOWN_PRICE
    except Exception as e:
        logger.warning(f"Pricing lookup failed for {provider}/{model_name}: {e}")
        return UNKNOWN_PRICE


def compute_cost(
    price: PriceInfo,
    input_tokens: int,
    output_tokens: int,
    cached_input_tokens: Optional[int] = None,
) -> Optional[float]:
    """Cost in USD for one call, or None when the price is unknown.

    `input_tokens` is the provider's total input count (LangChain's
    usage_metadata convention: cache reads are a subset of it). When a
    cache-read price is known those tokens are billed at it; otherwise all
    input is billed at the normal input price (a conservative upper bound).
    Rounded to 10 decimal places to avoid float noise in storage.
    """
    if price.status == "free":
        return 0.0
    if (
        price.status != "known"
        or price.input_per_token is None
        or price.output_per_token is None
    ):
        return None
    input_tokens = max(int(input_tokens or 0), 0)
    output_tokens = max(int(output_tokens or 0), 0)
    cached = max(min(int(cached_input_tokens or 0), input_tokens), 0)
    if cached and price.cached_input_per_token is not None:
        cost = (
            (input_tokens - cached) * price.input_per_token
            + cached * price.cached_input_per_token
        )
    else:
        cost = input_tokens * price.input_per_token
    cost += output_tokens * price.output_per_token
    return round(cost, 10)


async def get_price_per_token(provider: str, model_name: str) -> Tuple[float, float]:
    """Backward-compatible (input_price, output_price) USD per token.

    Returns (0.0, 0.0) for free AND unknown models - callers that need to
    distinguish the two must use `lookup_price()`. Never raises.
    """
    info = await lookup_price(provider, model_name)
    if info.status != "known":
        return (0.0, 0.0)
    return (info.input_per_token or 0.0, info.output_per_token or 0.0)
