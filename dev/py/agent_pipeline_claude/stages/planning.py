"""Planning stage (T3).

Produces a TaskPlan between context gathering and the ReAct loop.
Sets ctx.react_max_steps based on complexity assessment.
Graceful degradation: if the LLM call fails, the pipeline continues
without a plan using the default step budget.
"""

from __future__ import annotations

import time

import anthropic

from ..models import PipelineContext, TaskPlan, PLAN_SIZE_CONFIG, budget_from_plan
from ..infra._cli import CLI_GREEN, CLI_CLR
from ..infra.usage import summarize_anthropic_usage
from ..prompt_resources.prompt_manager import PromptManager


class PlanningStage:
    def __init__(self, client: anthropic.Anthropic, model: str, prompt_manager: PromptManager):
        self._client = client
        self._model = model
        self._prompt_manager = prompt_manager

    def execute(self, ctx: PipelineContext, logger) -> None:
        user_content = self._build_planning_input(ctx)
        try:
            resp = self._client.messages.parse(
                model=self._model,
                max_tokens=2048,
                system=self._prompt_manager.get("planning"),
                messages=[{"role": "user", "content": user_content}],
                output_format=TaskPlan,
            )
            logger.append_api_call({
                "stage": "planning",
                "ts": time.time(),
                "model": self._model,
                "input_fragment": user_content[:200],
                "usage": summarize_anthropic_usage(resp),
            })
            plan = resp.parsed_output
            plan = self._clamp_plan(plan)

            ctx.task_plan = plan
            ctx.plan_progress = [
                {"step_id": step.id, "done": False, "completed_at_react_step": None}
                for step in plan.steps
            ]

            ctx.react_max_steps = budget_from_plan(len(plan.steps))

            self._print_plan(plan, ctx.react_max_steps)

        except Exception as e:
            print(f"[PLAN] Failed ({e}), continuing without plan")
            ctx.task_plan = None

    @staticmethod
    def _build_planning_input(ctx: PipelineContext) -> str:
        parts = [f"Task: {ctx.task}"]
        if ctx.agents_md:
            parts.append(f"AGENTS.md ({ctx.agents_md_path}):\n{ctx.agents_md[:2000]}")
        if ctx.preloaded_context_files:
            for path, content in ctx.preloaded_context_files.items():
                excerpt = content[:1200]
                if len(content) > 1200:
                    excerpt += "\n...[truncated]"
                parts.append(f"--- {path} ---\n{excerpt}")
        if ctx.dfs_tree:
            parts.append(f"Filesystem tree:\n{ctx.dfs_tree}")
        if ctx.past_mistakes:
            parts.append("Past mistakes on this task:\n" + "\n".join(
                f"- {m.get('reason', '?')}" for m in ctx.past_mistakes[:3]
            ))
        return "\n\n".join(parts)

    @staticmethod
    def _clamp_plan(plan: TaskPlan) -> TaskPlan:
        """Ensure step count is within PLAN_SIZE_CONFIG bounds."""
        config = PLAN_SIZE_CONFIG.get(plan.complexity, PLAN_SIZE_CONFIG["complex"])
        if len(plan.steps) > config["max_steps"]:
            plan.steps = plan.steps[:config["max_steps"]]
        return plan

    @staticmethod
    def _print_plan(plan: TaskPlan, budget: int) -> None:
        print(f"{CLI_GREEN}[PLAN] Complexity: {plan.complexity} "
              f"({len(plan.steps)} steps, budget: {budget}){CLI_CLR}")
        for step in plan.steps:
            tools = ", ".join(step.expected_tools) if step.expected_tools else "—"
            print(f"  {step.id}. {step.description}  [{tools}]")
