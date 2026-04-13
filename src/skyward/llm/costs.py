"""
Token cost calculators for various LLM providers.

Costs are in USD per million tokens, updated December 2024.
"""

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
    # GPT-5 family
    "gpt-5": (1.25, 10.00),
    "gpt-5-mini": (0.50, 2.00),
    "gpt-5-nano": (0.05, 0.40),
    "gpt-5.1": (1.25, 10.00),
    "gpt-5.2": (1.75, 14.00),
    "gpt-5.2-pro": (21.00, 168.00),  # Reasoning model, ~12x more expensive
}

# Google Gemini pricing (per 1M tokens)
GEMINI_COSTS: Dict[str, Tuple[float, float]] = {
    # Gemini 1.x family
    "gemini-1.5-pro": (1.25, 5.00),
    "gemini-1.5-flash": (0.075, 0.30),
    # Gemini 2.x family
    "gemini-2.0-flash-exp": (0.00, 0.00),  # Free during preview
    "gemini-2.0-flash": (0.075, 0.30),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.5-pro": (1.25, 10.00),
    # Gemini 3.x family (preview)
    "gemini-3-flash-preview": (0.50, 3.00),
    "gemini-3-pro-preview": (2.00, 12.00),
}

# Anthropic pricing (per 1M tokens)
ANTHROPIC_COSTS: Dict[str, Tuple[float, float]] = {
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


def calculate_cost(
    input_tokens: int,
    output_tokens: int,
    model: str,
    provider: str = "openai",
) -> float:
    """
    Calculate the cost in USD for a given number of tokens.

    Parameters
    ----------
    input_tokens : int
        Number of input tokens
    output_tokens : int
        Number of output tokens
    model : str
        Model name
    provider : str
        Provider name: "openai", "gemini", or "perplexity"

    Returns
    -------
    float
        Cost in USD
    """
    if provider == "openai":
        costs = OPENAI_COSTS.get(model, (2.50, 10.00))  # Default to gpt-4o
    elif provider == "gemini":
        costs = GEMINI_COSTS.get(model, (0.075, 0.30))  # Default to flash
    elif provider == "perplexity":
        costs = PERPLEXITY_COSTS.get(model, (1.00, 1.00))  # Default to sonar
    elif provider == "anthropic":
        costs = ANTHROPIC_COSTS.get(model, (3.00, 15.00))  # Default to sonnet
    elif provider == "grok":
        costs = GROK_COSTS.get(model, (3.00, 15.00))  # Default to grok-3
    else:
        # Unknown provider, use conservative estimate
        costs = (2.50, 10.00)

    input_cost, output_cost = costs
    total_cost = (input_tokens * input_cost / 1_000_000) + (
        output_tokens * output_cost / 1_000_000
    )
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
