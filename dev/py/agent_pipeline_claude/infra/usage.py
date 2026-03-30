from __future__ import annotations


def summarize_anthropic_usage(response) -> dict | None:
    """Extract token usage from an Anthropic API response."""
    usage = getattr(response, "usage", None)
    if not usage:
        return None

    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    cache_creation = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
    cache_read = int(getattr(usage, "cache_read_input_tokens", 0) or 0)

    if input_tokens == 0 and output_tokens == 0:
        return None

    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_creation_input_tokens": cache_creation,
        "cache_read_input_tokens": cache_read,
        "total_tokens": input_tokens + output_tokens,
    }
