"""Pre-execution action validator (T5).

Validates tool calls against a rule set *before* dispatch.
Failures are logged to ctx.validation_log and returned to the LLM
as tool_result feedback so it can self-correct.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..models import AgentRuntimeContext

VALID_OUTCOMES = {
    "OUTCOME_OK",
    "OUTCOME_DENIED_SECURITY",
    "OUTCOME_NONE_CLARIFICATION",
    "OUTCOME_NONE_UNSUPPORTED",
    "OUTCOME_ERR_INTERNAL",
}

RULES: dict[str, list] = {
    "report_completion": [
        lambda args, ctx: (
            bool((args.get("message") or "").strip()),
            "message is empty — provide a non-empty message",
        ),
        lambda args, ctx: (
            args.get("outcome", "OUTCOME_OK") in VALID_OUTCOMES,
            f"invalid outcome code: {args.get('outcome')!r}",
        ),
    ],
    "write": [
        lambda args, ctx: (
            not (args.get("path") or "").startswith("/etc"),
            "writes to /etc are forbidden",
        ),
        lambda args, ctx: (
            bool((args.get("content") or "").strip()),
            "empty write content",
        ),
    ],
    "delete": [
        lambda args, ctx: (
            not (args.get("path") or "").startswith("/etc"),
            "deletes in /etc are forbidden",
        ),
    ],
}


class ActionValidator:
    """Stateless validator — call validate() before every tool dispatch."""

    def validate(
        self, tool_name: str, tool_input: dict, runtime_ctx: AgentRuntimeContext,
    ) -> tuple[bool, str]:
        pipeline_ctx = runtime_ctx.pipeline
        for rule in RULES.get(tool_name, []):
            passed, reason = rule(tool_input, pipeline_ctx)
            if not passed:
                pipeline_ctx.validation_log.append({
                    "tool": tool_name,
                    "args": tool_input,
                    "reason": reason,
                    "ts": time.time(),
                })
                return False, reason
        return True, ""


VALIDATION_PREFIX = "[VALIDATION_ERROR] "


def validate_or_error(
    runtime_ctx: AgentRuntimeContext, tool_name: str, tool_input: dict,
) -> str | None:
    """Helper for @function_tool functions.

    Returns an error string if validation fails, None if passes.
    """
    passed, reason = _VALIDATOR.validate(tool_name, tool_input, runtime_ctx)
    if not passed:
        return f"{VALIDATION_PREFIX}Validation failed: {reason}. Correct and retry."
    return None


_VALIDATOR = ActionValidator()
