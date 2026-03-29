from datetime import datetime, timezone

from .stages.context import ContextBuilderStage
from .infra.logger import RunLogger
from .models import PipelineContext, budget_from_plan
from .stages.planning import PlanningStage
from .prompt_resources.prompt_manager import PromptManager
from .stages.react import ReActLoopStage


def execute_context_builder(vm, model: str, prompt_manager: PromptManager,
                            ctx: PipelineContext, logger: RunLogger) -> None:
    ContextBuilderStage(vm, model, prompt_manager).execute(ctx, logger)


def build_initial_plan(model: str, prompt_manager: PromptManager,
                       ctx: PipelineContext, logger: RunLogger) -> None:
    PlanningStage(model, prompt_manager).execute(ctx, logger)
    if ctx.task_plan:
        ctx.react_max_steps = budget_from_plan(len(ctx.task_plan.steps))


def execute_react_loop(vm, model: str, prompt_manager: PromptManager,
                       ctx: PipelineContext, logger: RunLogger) -> None:
    ReActLoopStage(vm, model, prompt_manager).execute(ctx, logger)


def _write_run_result(ctx: PipelineContext, logger: RunLogger) -> None:
    logger.write_result({
        "final_answer": ctx.final_answer,
        "final_code": ctx.final_code,
        "files_used": ctx.files_used,
        "step_count": len(ctx.react_trace),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "backend": "openai_agents_v2",
        "planning_enabled": ctx.task_plan is not None,
        "plan_complexity": ctx.task_plan.complexity if ctx.task_plan else None,
        "plan_steps_count": len(ctx.task_plan.steps) if ctx.task_plan else None,
        "validation_rejections": len(ctx.validation_log),
        "loop_termination_reason": ctx.loop_termination_reason,
    })
    logger.write_trace()


def run_openai_pipeline(model: str, vm, task: str, task_id: str = "", run_dir=None) -> PipelineContext:
    """v2 pipeline: build_context → plan → execute → submit."""
    prompt_manager = PromptManager()
    ctx = PipelineContext(task=task, model=model)
    logger = RunLogger(task_id=task_id, run_dir=run_dir)

    logger.write_meta({
        "task_id": task_id,
        "model": model,
        "task": task,
        "task_fragment": task[:200],
        "started_at": datetime.now(timezone.utc).isoformat(),
        "backend": "openai_agents_v2",
        "prompt_versions": prompt_manager.active_versions(),
    })

    # Stage 1: Build context
    execute_context_builder(vm, model, prompt_manager, ctx, logger)

    # Stage 2: Plan (1 LLM call)
    if not ctx.pipeline_complete:
        build_initial_plan(model, prompt_manager, ctx, logger)

    # Stage 3: Execute (ReAct loop)
    if not ctx.pipeline_complete:
        execute_react_loop(vm, model, prompt_manager, ctx, logger)

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
        return run_openai_pipeline(
            model=self._model,
            vm=self._vm,
            task=self._task,
            task_id=self._task_id,
            run_dir=self._run_dir,
        )
