"""Planning stage (T3).

Produces a TaskPlan between context gathering and the ReAct loop.
Sets ctx.react_max_steps based on complexity assessment.
Graceful degradation: if the LLM call fails, the pipeline continues
without a plan using the default step budget.
"""

from __future__ import annotations

import time

from openai import OpenAI

from ..models import PipelineContext, TaskPlan, PLAN_SIZE_CONFIG, budget_from_plan
from ..infra._cli import CLI_GREEN, CLI_CLR
from ..infra.reasoning import supports_reasoning
from ..prompt_resources.prompt_manager import PromptManager


class PlanningStage:
    def __init__(self, model: str, reasoning: str | None,
                 prompt_manager: PromptManager, client: OpenAI):
        self._model = model
        self._reasoning = reasoning
        self._prompt_manager = prompt_manager
        self._client = client

    def execute(self, ctx: PipelineContext, logger) -> None:
        user_content = self._build_planning_input(ctx)
        try:
            instructions = self._prompt_manager.get("planning")
            api_kwargs = dict(
                model=self._model,
                messages=[
                    {"role": "system", "content": instructions},
                    {"role": "user", "content": user_content},
                ],
                response_format=TaskPlan,
            )
            if self._reasoning and supports_reasoning(self._model):
                api_kwargs["reasoning_effort"] = self._reasoning
            response = self._client.beta.chat.completions.parse(**api_kwargs)

            usage = response.usage
            logger.append_api_call({
                "stage": "planning",
                "ts": time.time(),
                "model": self._model,
                "input_fragment": user_content[:200],
                "usage": {
                    "input_tokens": usage.prompt_tokens or 0,
                    "output_tokens": usage.completion_tokens or 0,
                    "total_tokens": usage.total_tokens or 0,
                } if usage else None,
            })

            plan = response.choices[0].message.parsed
            if not isinstance(plan, TaskPlan):
                print("[PLAN] Unexpected output type, continuing without plan")
                return

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
        if ctx.vm_time:
            parts.append(f"Current VM time (use as 'today' for relative dates): {ctx.vm_time}")
        if ctx.agents_md:
            parts.append(f"AGENTS.md ({ctx.agents_md_path}):\n{ctx.agents_md}")
        if ctx.preloaded_context_files:
            for path, content in ctx.preloaded_context_files.items():
                excerpt = content[:1200]
                if len(content) > 1200:
                    excerpt += "\n...[truncated]"
                parts.append(f"--- {path} ---\n{excerpt}")
        if ctx.dfs_tree:
            parts.append(f"Filesystem tree:\n{ctx.dfs_tree}")
        if ctx.injection_risk_notes:
            parts.append(f"[SECURITY_ALERT] Context assessment flagged:\n{ctx.injection_risk_notes}")
        if ctx.past_mistakes:
            # Escalate security-related mistakes to top
            security_mistakes = [m for m in ctx.past_mistakes[:3]
                                 if "DENIED_SECURITY" in str(m.get("score_detail", []))]
            other_mistakes = [m for m in ctx.past_mistakes[:3]
                              if "DENIED_SECURITY" not in str(m.get("score_detail", []))]
            lines = []
            for m in security_mistakes:
                detail = m.get("score_detail", [])
                lines.append(f"- [SECURITY] {'; '.join(str(d) for d in detail[:3])}")
            for m in other_mistakes:
                reason = m.get("reason", "?")
                lines.append(f"- {reason}")
            parts.append("Past mistakes on this task:\n" + "\n".join(lines))
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
