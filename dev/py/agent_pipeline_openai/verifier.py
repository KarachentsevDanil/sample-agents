import json
import time

from .models import VerificationResult
from .prompt_manager import PromptManager

try:
    from agents import Agent, Runner
except ImportError:
    Agent = None
    Runner = None


class VerifierStage:
    def __init__(self, model: str, prompt_manager: PromptManager):
        self._model = model
        self._prompt_manager = prompt_manager

    def execute(self, ctx, logger) -> None:
        if not ctx.final_answer:
            ctx.verification_passed = False
            ctx.verification_reason = "No answer produced by ReAct loop"
            logger.append_mistake({
                "reason": ctx.verification_reason,
                "task_fragment": ctx.task[:200],
            })
            return

        if Agent is None or Runner is None:
            ctx.verification_passed = True
            ctx.verification_reason = "Verifier skipped: openai-agents not installed"
            return

        trace_summary = self._summarize_trace(ctx.react_trace)
        agent = Agent(
            name="Final Verifier",
            instructions=self._prompt_manager.get("verifier"),
            model=self._model,
            output_type=VerificationResult,
        )
        prompt = (
            f"Task: {ctx.task}\n\n"
            f"AGENTS.md instructions:\n{ctx.agents_md}\n\n"
            f"Reasoning trace summary:\n{trace_summary}\n\n"
            f"Final answer: {ctx.final_answer}\n"
        )
        try:
            result = Runner.run_sync(agent, input=prompt)
            parsed = result.final_output
            if isinstance(parsed, VerificationResult):
                ctx.verification_passed = parsed.passed
                ctx.verification_reason = parsed.reason
                logger.append_react_step({
                    "step": "verifier",
                    "node_id": str(len(ctx.react_trace) + 1),
                    "cmd": "verify_answer",
                    "args": {"task": ctx.task[:200], "answer": ctx.final_answer},
                    "result": json.dumps({"passed": parsed.passed, "reason": parsed.reason}),
                    "type": "validator_step",
                    "validation_passed": parsed.passed,
                    "ts": time.time(),
                })
            else:
                ctx.verification_passed = True
                ctx.verification_reason = "Verifier returned unexpected output type"
        except Exception as exc:
            ctx.verification_passed = True
            ctx.verification_reason = f"Verifier error (fail-open): {exc}"
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
