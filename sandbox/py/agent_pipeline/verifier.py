from openai import OpenAI

from .models import PipelineContext, VerificationResult
from .prompts import VERIFIER_PROMPT


class VerifierStage:
    def __init__(self, client: OpenAI, model: str):
        self._client = client
        self._model = model

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
        messages = [
            {"role": "system", "content": VERIFIER_PROMPT},
            {"role": "user", "content": (
                f"Task: {ctx.task}\n\n"
                f"AGENTS.md instructions:\n{ctx.agents_md}\n\n"
                f"Reasoning trace summary:\n{trace_summary}\n\n"
                f"Final answer: {ctx.final_answer}"
            )},
        ]

        try:
            resp = self._client.beta.chat.completions.parse(
                model=self._model,
                response_format=VerificationResult,
                messages=messages,
                max_completion_tokens=512,
            )
            result = resp.choices[0].message.parsed
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
            f"Step {s['step']}: {s['function']} — {s.get('result_summary', '')[:120]}"
            for s in trace
        ]
        return "\n".join(lines) if lines else "(no steps)"
