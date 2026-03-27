"""Pre-execution action validator (T5).

Validates tool calls against a rule set *before* dispatch.
Failures are logged to ctx.validation_log (not react_trace) and
returned to the LLM as tool_result feedback so it can self-correct.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import PipelineContext

VALID_OUTCOMES = {
    "OUTCOME_OK",
    "OUTCOME_DENIED_SECURITY",
    "OUTCOME_NONE_CLARIFICATION",
    "OUTCOME_NONE_UNSUPPORTED",
    "OUTCOME_ERR_INTERNAL",
}

# Each rule is (tool_input, ctx) -> (passed: bool, reason: str)
RULES: dict[str, list] = {
    "report_completion": [
        lambda args, ctx: (
            bool((args.get("message") or "").strip()),
            "message is empty — re-read relevant files and provide a non-empty message",
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
        self, tool_name: str, tool_input: dict, ctx: PipelineContext,
    ) -> tuple[bool, str]:
        for rule in RULES.get(tool_name, []):
            passed, reason = rule(tool_input, ctx)
            if not passed:
                ctx.validation_log.append({
                    "tool": tool_name,
                    "args": tool_input,
                    "reason": reason,
                    "ts": time.time(),
                })
                return False, reason
        return True, ""
