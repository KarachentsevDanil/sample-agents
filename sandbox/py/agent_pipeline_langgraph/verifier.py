from langgraph.runtime import Runtime

from agent_pipeline.models import VerificationResult
from agent_pipeline.prompts import VERIFIER_PROMPT

from .state import AgentState, GraphContext


def verifier_node(state: AgentState, runtime: Runtime[GraphContext]) -> dict:
    client = runtime.context.client
    logger = runtime.context.logger
    model = state["model"]
    final_answer = state.get("final_answer", "")

    if not final_answer:
        reason = "No answer produced by ReAct loop"
        logger.append_mistake({
            "reason": reason,
            "task_fragment": state["task"][:200],
        })
        return {"verification_passed": False, "verification_reason": reason}

    trace_summary = "\n".join(
        f"Step {s['step']}: {s['function']} — {s.get('result_summary', '')[:120]}"
        for s in state.get("react_trace", [])
    ) or "(no steps)"

    messages = [
        {"role": "system", "content": VERIFIER_PROMPT},
        {"role": "user", "content": (
            f"Task: {state['task']}\n\n"
            f"AGENTS.md instructions:\n{state.get('agents_md', '')}\n\n"
            f"Reasoning trace summary:\n{trace_summary}\n\n"
            f"Final answer: {final_answer}"
        )},
    ]

    try:
        resp = client.beta.chat.completions.parse(
            model=model,
            response_format=VerificationResult,
            messages=messages,
            max_completion_tokens=512,
        )
        result = resp.choices[0].message.parsed
        passed = result.passed
        reason = result.reason
    except Exception as e:
        return {
            "verification_passed": True,
            "verification_reason": f"Verifier error (fail-open): {e}",
        }

    if not passed:
        logger.append_mistake({
            "reason": reason,
            "answer_given": final_answer,
            "task_fragment": state["task"][:200],
            "files_used": state.get("files_used", []),
        })

    return {"verification_passed": passed, "verification_reason": reason}
