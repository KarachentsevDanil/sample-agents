from agent_pipeline.models import PipelineContext, VerificationResult
from agent_pipeline.prompts import VERIFIER_PROMPT
from openai_config import OPENAI_API_KEY

try:
    from langchain_core.messages import HumanMessage, SystemMessage
    from langchain_openai import ChatOpenAI
except ImportError:
    ChatOpenAI = None
    HumanMessage = None
    SystemMessage = None


class VerifierStage:
    def __init__(self, model: str):
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

        if ChatOpenAI is None:
            ctx.verification_passed = True
            ctx.verification_reason = "Verifier skipped: langchain-openai not installed"
            return

        llm = ChatOpenAI(
            model=self._model,
            api_key=OPENAI_API_KEY,
        ).with_structured_output(VerificationResult, method="function_calling")
        trace_summary = self._summarize_trace(ctx.react_trace)
        try:
            result = llm.invoke([
                SystemMessage(content=VERIFIER_PROMPT),
                HumanMessage(content=(
                    f"Task: {ctx.task}\n\n"
                    f"AGENTS.md instructions:\n{ctx.agents_md}\n\n"
                    f"Reasoning trace summary:\n{trace_summary}\n\n"
                    f"Final answer: {ctx.final_answer}"
                )),
            ])
            if isinstance(result, VerificationResult):
                ctx.verification_passed = result.passed
                ctx.verification_reason = result.reason
            elif isinstance(result, dict):
                ctx.verification_passed = bool(result.get("passed"))
                ctx.verification_reason = str(result.get("reason", ""))
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
            f"Step {s['step']}: {s['function']} — {s.get('result_summary', '')[:120]}"
            for s in trace
        ]
        return "\n".join(lines) if lines else "(no steps)"
