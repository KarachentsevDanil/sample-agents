from __future__ import annotations


def summarize_result_usage(result) -> dict | None:
    """Aggregate token usage across all raw model responses in an agents SDK RunResult."""
    raw_responses = getattr(result, "raw_responses", None) or []
    if not raw_responses:
        return None

    requests = 0
    input_tokens = 0
    cached_input_tokens = 0
    output_tokens = 0
    total_tokens = 0

    for raw in raw_responses:
        usage = getattr(raw, "usage", None)
        if not usage:
            continue
        requests += int(getattr(usage, "requests", 0) or 0)
        input_tokens += int(getattr(usage, "input_tokens", 0) or 0)
        input_details = getattr(usage, "input_tokens_details", None)
        cached_input_tokens += int(getattr(input_details, "cached_tokens", 0) or 0)
        output_tokens += int(getattr(usage, "output_tokens", 0) or 0)
        total_tokens += int(getattr(usage, "total_tokens", 0) or 0)

    if requests == 0 and input_tokens == 0 and cached_input_tokens == 0 and output_tokens == 0 and total_tokens == 0:
        return None

    return {
        "requests": requests,
        "input_tokens": input_tokens,
        "cached_input_tokens": cached_input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }
