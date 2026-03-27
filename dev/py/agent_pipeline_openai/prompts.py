from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import TaskPlan


def build_initial_user_message(
    task: str,
    agents_md: str,
    agents_md_path: str,
    dfs_tree: str,
    preread_files: dict,
    past_mistakes: list,
    task_plan: TaskPlan | None = None,
    context_blocks: list | None = None,
) -> str:
    parts = [f"[TASK]\n{task}"]

    # Insert context blocks section if any were selected
    if context_blocks:
        blocks_text = "\n\n".join(context_blocks)
        parts.append(f"[APPLICABLE RULES FOR THIS TASK]\n{blocks_text}")

    if agents_md:
        label = f"AGENTS.MD (canonical path: {agents_md_path})" if agents_md_path else "AGENTS.MD"
        parts.append(f"[{label} — mandatory instructions]\n{agents_md}")

    if dfs_tree:
        parts.append(f"[Filesystem outline — DFS from /]\n{dfs_tree}")

    for path, content in preread_files.items():
        parts.append(f"[Pre-read file: {path}]\n{content}")

    if past_mistakes:
        lines = []
        for m in past_mistakes:
            reason = m.get("reason", "unknown failure")
            entry = f"- MISTAKE: {reason}"
            # Include score_detail if available (shows expected vs actual)
            score_detail = m.get("score_detail")
            if score_detail and isinstance(score_detail, list):
                entry += f"\n  DETAIL: {'; '.join(str(d) for d in score_detail[:3])}"
            # Include the answer that was wrong (truncated)
            answer = m.get("answer_given") or m.get("agent_answer")
            if answer:
                entry += f"\n  YOUR WRONG ANSWER: {str(answer)[:120]}..."
            lines.append(entry)
        parts.append(f"[Past mistakes on this task — do not repeat]\n" + "\n".join(lines))

    if task_plan:
        plan_lines = [f"[EXECUTION PLAN — follow in order, note which step you're executing]"]
        for step in task_plan.steps:
            tools = ", ".join(step.expected_tools) if step.expected_tools else "—"
            plan_lines.append(f"  Step {step.id}: {step.description}  [tools: {tools}]")
        parts.append("\n".join(plan_lines))

    return "\n\n".join(parts)
