"""
Token cost calculators for various LLM providers.

Costs are in USD per million tokens, updated December 2024.
"""

import re
from typing import Dict, Tuple


# OpenAI pricing (per 1M tokens)
OPENAI_COSTS: Dict[str, Tuple[float, float]] = {
    # (input_cost, output_cost) per 1M tokens
    # GPT-4 family
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4.5-preview": (75.00, 150.00),
    "gpt-4-turbo": (10.00, 30.00),
    "gpt-3.5-turbo": (0.50, 1.50),
    # GPT-5 family (rates verified vs developers.openai.com/api/docs/pricing, Jun 2026)
    "gpt-5": (1.25, 10.00),
    "gpt-5-mini": (0.25, 2.00),   # was wrongly 0.50 input — undercounted/overcounted spend
    "gpt-5-nano": (0.05, 0.40),
    "gpt-5.1": (1.25, 10.00),
    "gpt-5.2": (1.75, 14.00),
    "gpt-5.2-pro": (21.00, 168.00),  # Reasoning model, ~12x more expensive
}

# Google Gemini pricing (per 1M tokens)
GEMINI_COSTS: Dict[str, Tuple[float, float]] = {
    # Gemini 2.x family
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.5-pro": (1.25, 10.00),
    # Gemini 3.x family (preview)
    "gemini-3-flash-preview": (0.50, 3.00),
    "gemini-3-pro-preview": (2.00, 12.00),
}

# Anthropic pricing (per 1M tokens) — verified vs platform.claude.com/docs pricing, Jun 2026
ANTHROPIC_COSTS: Dict[str, Tuple[float, float]] = {
    # Current models
    "claude-opus-4-8": (5.00, 25.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-sonnet-4-5": (3.00, 15.00),
    "claude-haiku-4-5-20251001": (1.00, 5.00),
    # Legacy / retired (kept for historical cost calc of old llm_log rows)
    "claude-opus-4-20250514": (15.00, 75.00),
    "claude-sonnet-4-20250514": (3.00, 15.00),
    "claude-haiku-3-5-20241022": (0.80, 4.00),
}

# Grok (xAI) pricing (per 1M tokens)
GROK_COSTS: Dict[str, Tuple[float, float]] = {
    "grok-3": (3.00, 15.00),
    "grok-3-mini": (0.30, 0.50),
    "grok-3-fast": (5.00, 25.00),
}

# Perplexity pricing (per 1M tokens)
PERPLEXITY_COSTS: Dict[str, Tuple[float, float]] = {
    "sonar": (1.00, 1.00),
    "sonar-pro": (3.00, 15.00),
    "sonar-reasoning-pro": (2.00, 8.00),
}


# Explicit per-model cached-input (cache-read) rates per 1M tokens, used where the
# discount isn't a single provider-wide multiplier. Verified Jun 2026:
#   OpenAI gpt-5 family bills cache reads at 10% of input; gpt-4o family at 50%.
#   Anthropic cache hits = 10% of input.
CACHED_INPUT_COSTS: Dict[str, float] = {
    # OpenAI
    "gpt-5": 0.125, "gpt-5-mini": 0.025, "gpt-5-nano": 0.005,
    "gpt-4o": 1.25, "gpt-4o-mini": 0.075,
    # Anthropic (cache read = 0.1x input)
    "claude-opus-4-8": 0.50, "claude-sonnet-4-6": 0.30, "claude-sonnet-4-5": 0.30,
    "claude-haiku-4-5-20251001": 0.10,
}

# Fallback cache-read discount (fraction of input rate) when a model is not in
# CACHED_INPUT_COSTS, plus the cache-write (creation) multiplier, per provider.
CACHE_READ_MULTIPLIER: Dict[str, float] = {
    "anthropic": 0.1, "openai": 0.5, "gemini": 0.25, "grok": 0.25, "perplexity": 1.0,
}
CACHE_WRITE_MULTIPLIER: Dict[str, float] = {  # Anthropic 5m cache write = 1.25x input
    "anthropic": 1.25, "openai": 1.0, "gemini": 1.0, "grok": 1.0, "perplexity": 1.0,
}

# Providers whose reported input_tokens INCLUDE cached tokens — for these, cache
# reads are subtracted from input so they aren't billed twice. Anthropic reports
# cached tokens separately, so it is NOT in this set.
_INPUT_INCLUDES_CACHE = {"openai", "gemini", "perplexity", "grok"}

_PROVIDER_TABLES = {
    "openai": (OPENAI_COSTS, (2.50, 10.00)),
    "gemini": (GEMINI_COSTS, (0.30, 2.50)),
    "perplexity": (PERPLEXITY_COSTS, (1.00, 1.00)),
    "anthropic": (ANTHROPIC_COSTS, (3.00, 15.00)),
    "grok": (GROK_COSTS, (3.00, 15.00)),
}

_MODEL_DATE_SUFFIX = re.compile(r"-\d{4}-\d{2}-\d{2}$")


def _strip_model_suffix(model: str) -> str:
    """Strip a trailing -YYYY-MM-DD date suffix (e.g. gpt-5-mini-2025-08-07 -> gpt-5-mini)."""
    return _MODEL_DATE_SUFFIX.sub("", model or "")


def calculate_cost(
    input_tokens: int,
    output_tokens: int,
    model: str,
    provider: str = "openai",
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> float:
    """
    Calculate the cost in USD for a call.

    Parameters
    ----------
    input_tokens : int
        Provider-reported prompt token count. For OpenAI/Gemini/Perplexity/Grok
        this INCLUDES cached tokens (so cache reads are subtracted before billing
        the standard input rate). For Anthropic, cached tokens are reported
        separately and input_tokens is billed in full.
    output_tokens : int
        Output tokens.
    model : str
        Model id; a trailing date suffix (``-YYYY-MM-DD``) is matched to its family.
    provider : str
        "openai", "gemini", "perplexity", "anthropic", or "grok".
    cache_read_tokens : int
        Prompt-cache read (hit) tokens, billed at the model's cached-input rate.
    cache_write_tokens : int
        Prompt-cache write/creation tokens (Anthropic), billed at the write rate.

    Returns
    -------
    float
        Cost in USD.
    """
    base = _strip_model_suffix(model)
    table, defaults = _PROVIDER_TABLES.get(provider, (None, (2.50, 10.00)))
    input_cost, output_cost = table.get(base, defaults) if table is not None else defaults

    # cached-input (read) rate per 1M tokens
    cache_read_cost = CACHED_INPUT_COSTS.get(
        base, input_cost * CACHE_READ_MULTIPLIER.get(provider, 1.0)
    )

    billable_input = input_tokens
    if provider in _INPUT_INCLUDES_CACHE and cache_read_tokens:
        billable_input = max(0, input_tokens - cache_read_tokens)

    total_cost = (billable_input * input_cost / 1_000_000) + (
        output_tokens * output_cost / 1_000_000
    )
    if cache_read_tokens:
        total_cost += cache_read_tokens * cache_read_cost / 1_000_000
    if cache_write_tokens:
        write_cost = input_cost * CACHE_WRITE_MULTIPLIER.get(provider, 1.0)
        total_cost += cache_write_tokens * write_cost / 1_000_000
    return total_cost


def estimate_batch_cost(
    num_items: int = None,
    num_pages: int = None,
    questions_per_page: int = 1,
    avg_input_tokens: int = 3000,
    avg_output_tokens: int = 1000,
    model: str = "gpt-4o",
    provider: str = "openai",
) -> Dict[str, float]:
    """
    Estimate the cost for a batch of LLM calls.

    Parameters
    ----------
    num_items : int, optional
        Number of items to process (use this OR num_pages)
    num_pages : int, optional
        Number of pages to process (for FAQ-style batch processing)
    questions_per_page : int
        Number of questions per page (multiplied by num_pages)
    avg_input_tokens : int
        Average input tokens per item
    avg_output_tokens : int
        Average output tokens per item
    model : str
        Model name
    provider : str
        Provider name

    Returns
    -------
    Dict[str, float]
        {"total_cost": float, "cost_per_item": float, "estimated_tokens": int}
    """
    # Support both num_items and num_pages for backwards compatibility
    if num_pages is not None:
        total_questions = num_pages * questions_per_page
    elif num_items is not None:
        total_questions = num_items
    else:
        raise ValueError("Either num_items or num_pages must be provided")

    total_input = total_questions * avg_input_tokens
    total_output = total_questions * avg_output_tokens
    total_cost = calculate_cost(total_input, total_output, model, provider)

    return {
        "total_cost": total_cost,
        "cost_per_item": total_cost / total_questions if total_questions > 0 else 0,
        "estimated_input_tokens": total_input,
        "estimated_output_tokens": total_output,
    }


def format_cost(cost: float) -> str:
    """Format cost as a readable string."""
    if cost < 0.01:
        return f"${cost:.4f}"
    elif cost < 1.00:
        return f"${cost:.3f}"
    else:
        return f"${cost:.2f}"


def summarize_costs(
    # Generic dict-based interface
    token_counts: Dict[str, Tuple[int, int]] = None,
    models: Dict[str, Tuple[str, str]] = None,
    # FAQ pipeline-specific interface (backwards compatibility)
    enrichment_tokens: Tuple[int, int] = None,
    answer_tokens: Tuple[int, int] = None,
    fact_check_tokens: Tuple[int, int] = None,
    enrichment_model: str = "gpt-4o-mini",
    answer_model: str = "gpt-4o",
    answer_provider: str = "openai",
    fact_check_model: str = "sonar",
) -> Dict[str, float]:
    """
    Summarize total costs across multiple pipeline steps.

    Supports two interfaces:
    1. Generic dict-based: token_counts and models dicts
    2. FAQ pipeline-specific: individual token tuples and model names

    Parameters
    ----------
    token_counts : Dict[str, Tuple[int, int]], optional
        Dictionary mapping step name to (input_tokens, output_tokens)
    models : Dict[str, Tuple[str, str]], optional
        Dictionary mapping step name to (model, provider)
    enrichment_tokens : Tuple[int, int], optional
        (input, output) tokens for enrichment step
    answer_tokens : Tuple[int, int], optional
        (input, output) tokens for answer generation step
    fact_check_tokens : Tuple[int, int], optional
        (input, output) tokens for fact checking step
    enrichment_model : str
        Model used for enrichment (default: gpt-4o-mini)
    answer_model : str
        Model used for answer generation (default: gpt-4o)
    answer_provider : str
        Provider for answer generation (default: openai)
    fact_check_model : str
        Model used for fact checking (default: sonar)

    Returns
    -------
    Dict[str, float]
        Cost breakdown by step and total
    """
    # If using dict-based interface
    if token_counts is not None:
        result = {}
        total = 0.0

        for step, (input_tokens, output_tokens) in token_counts.items():
            model, provider = (models or {}).get(step, ("gpt-4o", "openai"))
            cost = calculate_cost(input_tokens, output_tokens, model, provider)
            result[f"{step}_cost"] = cost
            total += cost

        result["total_cost"] = total
        return result

    # FAQ pipeline-specific interface
    result = {}
    total = 0.0

    if enrichment_tokens:
        cost = calculate_cost(
            enrichment_tokens[0], enrichment_tokens[1],
            enrichment_model, "openai"
        )
        result["enrichment_cost"] = cost
        total += cost

    if answer_tokens:
        cost = calculate_cost(
            answer_tokens[0], answer_tokens[1],
            answer_model, answer_provider
        )
        result["answer_cost"] = cost
        total += cost

    if fact_check_tokens:
        cost = calculate_cost(
            fact_check_tokens[0], fact_check_tokens[1],
            fact_check_model, "perplexity"
        )
        result["fact_check_cost"] = cost
        total += cost

    result["total_cost"] = total
    return result
