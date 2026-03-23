import time

import anthropic

from agent_pipeline.models import PipelineContext, VerificationResult
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
        try:
            resp = self._client.messages.parse(
                model=self._model,
                max_tokens=512,
                system=self._prompt_manager.get("verifier"),
                messages=[
                    {
                        "role": "user",
                        "content": (
                            f"Task: {ctx.task}\n\n"
                            f"AGENTS.md instructions:\n{ctx.agents_md}\n\n"
                            f"Reasoning trace summary:\n{trace_summary}\n\n"
                            f"Final answer: {ctx.final_answer}"
                        ),
                    }
                ],
                output_format=VerificationResult,
            )
            logger.append_api_call({
                "stage": "verifier",
                "ts": time.time(),
                "model": self._model,
                "system": self._prompt_manager.get("verifier"),
                "messages": [{"role": "user", "content": (
                    f"Task: {ctx.task}\n\n"
                    f"AGENTS.md instructions:\n{ctx.agents_md}\n\n"
                    f"Reasoning trace summary:\n{trace_summary}\n\n"
                    f"Final answer: {ctx.final_answer}"
                )}],
                "response_stop_reason": getattr(resp, "stop_reason", None),
                "response_content": [b.model_dump() for b in resp.content] if hasattr(resp, "content") else [],
                "usage": resp.usage.model_dump() if getattr(resp, "usage", None) else None,
            })
            result = resp.parsed_output
            ctx.verification_passed = result.passed
            ctx.verification_reason = result.reason
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
