import json
import time

import anthropic

from .models import PipelineContext, VerificationResult
from ._logging import build_api_log_entry
from .prompt_manager import PromptManager


class VerifierStage:
    def __init__(self, client: anthropic.Anthropic, model: str, prompt_manager: PromptManager):
        self._client = client
        self._model = model
        self._prompt_manager = prompt_manager

    def execute(self, ctx: PipelineContext, logger) -> None:
        if not ctx.final_answer:
            ctx.verification_passed = False
            ctx.verification_reason = "No answer produced by ReAct loop"
            logger.append_mistake({
                "reason": ctx.verification_reason,
                "task_fragment": ctx.task[:200],
            })
            return

        trace_summary = self._summarize_trace(ctx.react_trace)
        user_content = (
            f"Task: {ctx.task}\n\n"
            f"AGENTS.md instructions:\n{ctx.agents_md}\n\n"
            f"Reasoning trace summary:\n{trace_summary}\n\n"
            f"Final answer: {ctx.final_answer}"
        )
        try:
            resp = self._client.messages.parse(
                model=self._model,
                max_tokens=512,
                system=self._prompt_manager.get("verifier"),
                messages=[{"role": "user", "content": user_content}],
                output_format=VerificationResult,
            )
            logger.append_api_call(build_api_log_entry(
                "verifier", self._model, self._prompt_manager.get("verifier"),
                [{"role": "user", "content": user_content}], resp,
            ))
            result = resp.parsed_output
            ctx.verification_passed = result.passed
            ctx.verification_reason = result.reason
            ctx.node_counter += 1
            logger.append_react_step({
                "step": "verifier",
                "node_id": str(ctx.node_counter),
                "cmd": "verify_answer",
                "args": {"task": ctx.task[:200], "answer": ctx.final_answer},
                "result": json.dumps({"passed": result.passed, "reason": result.reason}),
                "type": "validator_step",
                "validation_passed": result.passed,
                "ts": time.time(),
            })
        except Exception as e:
            ctx.verification_passed = True
            ctx.verification_reason = f"Verifier error (fail-open): {e}"
            return

        if not ctx.verification_passed:
            logger.append_mistake({
                "reason": ctx.verification_reason,
                "answer_given": ctx.final_answer,
                "task_fragment": ctx.task[:200],
                "files_used": ctx.files_used,
            })

    @staticmethod
    def _summarize_trace(trace: list) -> str:
        lines = [
            f"Step {i + 1}: {s.get('cmd', '?')} — {str(s.get('args', ''))[:120]}"
            for i, s in enumerate(trace)
        ]
        return "\n".join(lines) if lines else "(no steps)"
