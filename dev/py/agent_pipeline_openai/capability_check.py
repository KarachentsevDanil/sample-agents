"""Capability pre-check stage.

Scans the task text for patterns that require unavailable capabilities
(email, HTTP, calendar, CRM, etc.) and short-circuits the pipeline with
OUTCOME_NONE_UNSUPPORTED — without running the ReAct loop.

This eliminates the failure class where the agent wastes 5-30 steps
reasoning about an impossible task before giving up.
"""

from __future__ import annotations

import re
import time

from .models import PipelineContext

# (regex_pattern, capability_label)
# Patterns are case-insensitive. Order matters: first match wins.
_UNSUPPORTED_PATTERNS: list[tuple[str, str]] = [
    # Email — "Email Sam", "send an email to", "write email to bob@"
    # Negative lookahead avoids false-positive on "email address", "email of", "email from"
    (r"\bemail\s+(?!address\b|from\b|of\b|in\b|the\b|a\b)\w+", "sending email"),
    (r"\bsend\s+.*\bemail\b", "sending email"),
    (r"\bwrite\s+.*\bemail\s+to\b", "sending email"),
    # Calendar
    (r"\bcalendar\s+invite\b", "calendar integration"),
    (r"\bcreate\s+a\s+calendar\b", "calendar integration"),
    (r"\bschedule\s+.*\bmeeting\b", "calendar integration"),
    (r"\bschedule\s+.*\binvite\b", "calendar integration"),
    # HTTP / web upload
    (r"\bupload\s+.*\bhttps?://", "HTTP upload"),
    (r"\bpost\s+.*\bhttps?://", "HTTP POST"),
    (r"\bpush\s+.*\bhttps?://", "HTTP push"),
    (r"\bsend\s+.*\bhttps?://", "HTTP request"),
    # Salesforce / CRM
    (r"\bsync\s+.*\bsalesforce\b", "Salesforce CRM sync"),
    (r"\bpush\s+.*\bsalesforce\b", "Salesforce CRM sync"),
    # Slack / Teams
    (r"\bsend\s+.*\bslack\b", "Slack messaging"),
    (r"\bslack\s+message\b", "Slack messaging"),
    # Webhooks
    (r"\bwebhook\b", "webhook"),
    # Notifications
    (r"\bsend\s+.*\bnotification\b", "push notification"),
]


def _detect_unsupported(task: str) -> tuple[str, str] | None:
    """Return (capability_label, matched_pattern) if task requires an unsupported capability."""
    lower = task.lower()
    for pattern, label in _UNSUPPORTED_PATTERNS:
        if re.search(pattern, lower):
            return label, pattern
    return None


class CapabilityCheckStage:
    """Pre-stage that detects unsupported capabilities and exits early.

    Runs before the ReAct loop. If the task requires email, HTTP, calendar,
    Salesforce, etc., submits OUTCOME_NONE_UNSUPPORTED to the harness and
    sets ctx.pipeline_complete = True so no further stages execute.
    """

    def __init__(self, vm) -> None:
        self._vm = vm

    def execIute(self, ctx: PipelineContext, logger) -> None:
        match = _detect_unsupported(ctx.task)
        if match is None:
            return  # Task looks feasible — let pipeline continue

        capability, _ = match
        message = (
            f"Cannot complete task — requires {capability}, "
            f"which is not available in this environment."
        )

        print(f"[CAP_CHECK] Blocked: task requires {capability!r}")

        ctx.final_answer = message
        ctx.final_code = "OUTCOME_NONE_UNSUPPORTED"
        ctx.capability_blocked = True
        ctx.pipeline_complete = True
        ctx.loop_termination_reason = "capability_blocked"

        # Submit answer to benchmark harness
        try:
            from bitgn.vm.pcm_pb2 import AnswerRequest, Outcome
            self._vm.answer(AnswerRequest(
                message=message,
                outcome=Outcome.OUTCOME_NONE_UNSUPPORTED,
                refs=[],
            ))
        except Exception as exc:
            print(f"[CAP_CHECK] Warning: failed to submit answer to harness: {exc}")

        logger.append_api_call({
            "stage": "capability_check",
            "ts": time.time(),
            "capability": capability,
            "blocked": True,
            "message": message,
        })
