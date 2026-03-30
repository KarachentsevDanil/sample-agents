import os
from datetime import datetime, timezone

import anthropic
from dotenv import load_dotenv

load_dotenv()

from .stages.context import ContextBuilderStage
from .stages.planning import PlanningStage
from .stages.react import ReActLoopStage
from .infra.logger import RunLogger
from .models import PipelineContext, budget_from_plan
from .prompt_resources.prompt_manager import PromptManager


def execute_context_builder(vm, client: anthropic.Anthropic, model: str,
                            prompt_manager: PromptManager,
                            ctx: PipelineContext, logger: RunLogger) -> None:
    ContextBuilderStage(vm, client, model, prompt_manager).execute(ctx, logger)


def build_initial_plan(client: anthropic.Anthropic, model: str,
                       prompt_manager: PromptManager,
                       ctx: PipelineContext, logger: RunLogger) -> None:
    PlanningStage(client, model, prompt_manager).execute(ctx, logger)
    if ctx.task_plan:
        ctx.react_max_steps = budget_from_plan(len(ctx.task_plan.steps))


def execute_react_loop(vm, client: anthropic.Anthropic, model: str,
                       prompt_manager: PromptManager,
                       ctx: PipelineContext, logger: RunLogger) -> None:
    ReActLoopStage(vm, client, model, prompt_manager).execute(ctx, logger)


def _write_run_result(ctx: PipelineContext, logger: RunLogger) -> None:
    logger.write_result({
        "final_answer": ctx.final_answer,
        "final_code": ctx.final_code,
        "files_used": ctx.files_used,
        "step_count": len(ctx.react_trace),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "backend": "anthropic",
        "planning_enabled": ctx.task_plan is not None,
        "plan_complexity": ctx.task_plan.complexity if ctx.task_plan else None,
        "plan_steps_count": len(ctx.task_plan.steps) if ctx.task_plan else None,
        "validation_rejections": len(ctx.validation_log),
        "loop_termination_reason": ctx.loop_termination_reason,
    })
    logger.write_trace()


def run_claude_pipeline(model: str, vm, task: str, task_id: str = "", run_dir=None) -> PipelineContext:
    """Main pipeline: build_context → plan → execute → submit."""
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    prompt_manager = PromptManager()
    ctx = PipelineContext(task=task, model=model)
    logger = RunLogger(task_id=task_id, run_dir=run_dir)

    logger.write_meta({
        "task_id": task_id,
        "model": model,
        "task": task,
        "task_fragment": task[:200],
        "started_at": datetime.now(timezone.utc).isoformat(),
        "backend": "anthropic",
        "prompt_versions": prompt_manager.active_versions(),
    })

    # Stage 1: Build context
    execute_context_builder(vm, client, model, prompt_manager, ctx, logger)

    # Stage 2: Plan (1 LLM call)
    if not ctx.pipeline_complete:
        build_initial_plan(client, model, prompt_manager, ctx, logger)

    # Stage 3: Execute (ReAct loop)
    if not ctx.pipeline_complete:
        execute_react_loop(vm, client, model, prompt_manager, ctx, logger)

    _write_run_result(ctx, logger)
    return ctx


class Pipeline:
    """Compatibility wrapper."""

    def __init__(self, model: str, vm, task: str, task_id: str = "", run_dir=None):
        self._model = model
        self._vm = vm
        self._task = task
        self._task_id = task_id
        self._run_dir = run_dir

    def use_capability_check(self) -> "Pipeline":
        return self

    def use_context(self) -> "Pipeline":
        return self

    def use_planning(self) -> "Pipeline":
        return self

    def use_react(self) -> "Pipeline":
        return self

    def use_response_verifier(self) -> "Pipeline":
        return self

    def use_retry(self) -> "Pipeline":
        return self

    def run(self) -> PipelineContext:
        return run_claude_pipeline(
            model=self._model,
            vm=self._vm,
            task=self._task,
            task_id=self._task_id,
            run_dir=self._run_dir,
        )
