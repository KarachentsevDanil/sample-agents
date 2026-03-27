import time


def build_api_log_entry(
    stage: str, model: str, system: str, messages: list, response, **extra
) -> dict:
    thinking = ""
    if hasattr(response, "content"):
        for block in response.content:
            if getattr(block, "type", None) == "thinking":
                thinking = getattr(block, "thinking", "")
                break
    return {
        "stage": stage,
        "ts": time.time(),
        "model": model,
        "system": system,
        "messages": messages,
        "thinking": thinking,
        "response_stop_reason": getattr(response, "stop_reason", None),
        "response_content": [b.model_dump() for b in response.content] if hasattr(response, "content") else [],
        "usage": response.usage.model_dump() if getattr(response, "usage", None) else None,
        **extra,
    }
