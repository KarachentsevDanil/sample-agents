"""Reasoning effort support check for OpenAI models."""


def supports_reasoning(model: str) -> bool:
    """Check if a model supports the reasoning_effort parameter."""
    return "nano" not in model.lower()
