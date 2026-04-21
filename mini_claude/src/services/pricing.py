"""Per-model cost tracking and calculation.

Reference: Claude/OpenAI pricing pages
"""

from typing import Optional, Dict

# Pricing: $ per 1M tokens
MODEL_PRICING: Dict[str, Dict[str, float]] = {
    # Claude 4 family
    "claude-opus-4": {"input": 15.0, "output": 75.0, "cache_read": 1.5, "cache_write": 18.75},
    "claude-sonnet-4": {"input": 3.0, "output": 15.0, "cache_read": 0.3, "cache_write": 3.75},
    # Claude 3.5 family
    "claude-3-5-sonnet": {"input": 3.0, "output": 15.0, "cache_read": 0.3, "cache_write": 3.75},
    "claude-3-5-haiku": {"input": 0.8, "output": 4.0, "cache_read": 0.08, "cache_write": 1.0},
    # Claude 3 family
    "claude-3-opus": {"input": 15.0, "output": 75.0, "cache_read": 1.5, "cache_write": 18.75},
    "claude-3-sonnet": {"input": 3.0, "output": 15.0},
    "claude-3-haiku": {"input": 0.25, "output": 1.25},
    # OpenAI
    "gpt-4o": {"input": 2.5, "output": 10.0},
    "gpt-4o-mini": {"input": 0.15, "output": 0.6},
    "gpt-4-turbo": {"input": 10.0, "output": 30.0},
    "gpt-4": {"input": 30.0, "output": 60.0},
    "gpt-3.5-turbo": {"input": 0.5, "output": 1.5},
    "o1": {"input": 15.0, "output": 60.0},
    "o1-mini": {"input": 3.0, "output": 12.0},
    "o3-mini": {"input": 1.1, "output": 4.4},
    # DeepSeek
    "deepseek-chat": {"input": 0.14, "output": 0.28},
    "deepseek-reasoner": {"input": 0.55, "output": 2.19},
}

# Fallback pricing when model is not found
_FALLBACK_PRICING = {"input": 3.0, "output": 15.0}


def match_model(model: str) -> Optional[Dict[str, float]]:
    """Match a model string to its pricing using prefix matching.

    Examples:
        "claude-sonnet-4-20250514" -> matches "claude-sonnet-4"
        "anthropic/claude-3-5-sonnet-20241022" -> matches "claude-3-5-sonnet"
        "gpt-4o-2024-08-06" -> matches "gpt-4o"
    """
    # Strip provider prefix (e.g., "anthropic/", "openai/")
    normalized = model
    if "/" in normalized:
        normalized = normalized.split("/", 1)[1]

    # Exact match first
    if normalized in MODEL_PRICING:
        return MODEL_PRICING[normalized]

    # Prefix match: try progressively shorter prefixes
    # Sort keys by length descending so longer (more specific) matches win
    for key in sorted(MODEL_PRICING.keys(), key=len, reverse=True):
        if normalized.startswith(key):
            return MODEL_PRICING[key]

    return None


def calculate_cost(model: str, usage: "TokenUsage") -> float:
    """Calculate cost in dollars for given model and usage."""
    pricing = match_model(model) or _FALLBACK_PRICING

    cost = 0.0
    cost += usage.input_tokens * pricing.get("input", 0) / 1_000_000
    cost += usage.output_tokens * pricing.get("output", 0) / 1_000_000
    cost += usage.cache_read_tokens * pricing.get("cache_read", pricing.get("input", 0) * 0.1) / 1_000_000
    cost += usage.cache_write_tokens * pricing.get("cache_write", pricing.get("input", 0) * 1.25) / 1_000_000

    return cost


def format_cost_report(model: str, usage: "TokenUsage") -> str:
    """Format a human-readable cost report."""
    cost = calculate_cost(model, usage)
    pricing = match_model(model)
    pricing_source = "known" if pricing else "fallback"

    lines = [
        f"Model: {model} ({pricing_source} pricing)",
        f"Input tokens:       {usage.input_tokens:>10,}",
        f"Output tokens:      {usage.output_tokens:>10,}",
    ]
    if usage.cache_read_tokens:
        lines.append(f"Cache read tokens:  {usage.cache_read_tokens:>10,}")
    if usage.cache_write_tokens:
        lines.append(f"Cache write tokens: {usage.cache_write_tokens:>10,}")
    lines.append(f"Total tokens:       {usage.total:>10,}")
    lines.append(f"Estimated cost:     ${cost:>9.4f}")

    return "\n".join(lines)
