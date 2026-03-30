"""Structured context document builder for Claude pipeline.

Assembles everything the model needs into a single organized document
so the LLM gets maximum context with zero tool calls.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..models import PipelineContext


def build_initial_user_message(ctx: PipelineContext) -> str:
    """Build structured context document from PipelineContext."""
    sections = []

    # 1. Task
    sections.append(f"# TASK\n{ctx.task}")

    # 2. VM Time (critical for date calculations)
    if ctx.vm_time:
        sections.append(f"# CURRENT TIME\n{ctx.vm_time}")

    # 3. Authority: AGENTS.md
    if ctx.agents_md:
        label = f"AGENTS.MD (canonical path: {ctx.agents_md_path})" if ctx.agents_md_path else "AGENTS.MD"
        sections.append(f"# {label} — mandatory authority\n{ctx.agents_md}")

    # 4. Trusted rule files (pre-loaded by context agent)
    for path, content in ctx.preloaded_context_files.items():
        excerpt = content[:2000]
        if len(content) > 2000:
            excerpt += "\n...[truncated]"
        sections.append(f"# TRUSTED RULE: {path}\n{excerpt}")

    # 5. Filesystem tree
    if ctx.dfs_tree:
        sections.append(f"# FILESYSTEM TREE\n{ctx.dfs_tree}")

    # 6. Past mistakes
    if ctx.past_mistakes:
        lines = []
        for m in ctx.past_mistakes[:3]:
            reason = m.get("reason", "unknown")
            detail = m.get("score_detail", [])
            lines.append(f"- {reason}")
            if detail:
                lines.append(f"  Detail: {'; '.join(str(d) for d in detail[:3])}")
            answer = m.get("answer_given") or m.get("agent_answer")
            if answer:
                lines.append(f"  YOUR WRONG ANSWER: {str(answer)[:120]}...")
        sections.append("# PAST MISTAKES (do not repeat)\n" + "\n".join(lines))

    # 7. Execution plan
    if ctx.task_plan:
        plan_lines = ["# EXECUTION PLAN — follow in order"]
        for step in ctx.task_plan.steps:
            tools = ", ".join(step.expected_tools) if step.expected_tools else "—"
            plan_lines.append(f"  Step {step.id}: {step.description}  [tools: {tools}]")
        sections.append("\n".join(plan_lines))

    return "\n\n".join(sections)
