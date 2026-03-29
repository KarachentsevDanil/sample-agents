import json
import time

from .models import VerificationResult
from .prompt_manager import PromptManager
from .usage import summarize_result_usage

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
        prompt_parts = [
            f"Task: {ctx.task}",
            f"AGENTS.md instructions:\n{ctx.agents_md}",
        ]
        if ctx.rule_graph_summary:
            prompt_parts.append(f"Trusted rule graph:\n{ctx.rule_graph_summary}")
        if ctx.task_relevant_rule_summaries:
            prompt_parts.append(
                "Task-relevant trusted rules:\n" +
                "\n".join(f"- {rule}" for rule in ctx.task_relevant_rule_summaries)
            )
        if ctx.preloaded_context_files:
            for path, content in ctx.preloaded_context_files.items():
                excerpt = content[:1200]
                if len(content) > 1200:
                    excerpt += "\n...[truncated]"
                prompt_parts.append(f"Trusted rule file: {path}\n{excerpt}")
        prompt_parts.append(f"Reasoning trace summary:\n{trace_summary}")
        prompt_parts.append(f"Final answer: {ctx.final_answer}")
        prompt = "\n\n".join(prompt_parts)
        try:
            result = Runner.run_sync(agent, input=prompt)
            logger.append_api_call({
                "stage": "verifier",
                "ts": time.time(),
                "model": self._model,
                "usage": summarize_result_usage(result),
            })
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
