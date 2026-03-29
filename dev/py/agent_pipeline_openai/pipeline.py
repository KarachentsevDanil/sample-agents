from datetime import datetime, timezone

from .context import ContextBuilderStage
from .logger import RunLogger
from .models import PipelineContext
from .planning import PlanningStage
from .prompt_manager import PromptManager
from .react import ReActLoopStage
from .verifier import VerifierStage


def execute_context_builder(vm, model: str, prompt_manager: PromptManager,
                            ctx: PipelineContext, logger: RunLogger) -> None:
    ContextBuilderStage(vm, model, prompt_manager).execute(ctx, logger)


def build_initial_plan(model: str, prompt_manager: PromptManager,
                       ctx: PipelineContext, logger: RunLogger) -> None:
    PlanningStage(model, prompt_manager).execute(ctx, logger)


def execute_react_loop(vm, model: str, prompt_manager: PromptManager,
                       ctx: PipelineContext, logger: RunLogger) -> None:
    ReActLoopStage(vm, model, prompt_manager).execute(ctx, logger)


def verify_final_answer(model: str, prompt_manager: PromptManager,
                        ctx: PipelineContext, logger: RunLogger) -> None:
    VerifierStage(model, prompt_manager).execute(ctx, logger)


def retry_react_once(vm, model: str, prompt_manager: PromptManager,
                     ctx: PipelineContext, logger: RunLogger) -> None:
    """Retry the ReAct loop once when no answer was produced.

    Only retry when the first pass really ended without a recorded answer.
    If report_completion succeeded, ctx.final_answer/final_code should already
    be set directly by the tool implementation and this branch is skipped.
    """
    if ctx.final_answer or ctx.capability_blocked or ctx.pipeline_complete:
        return

    print("[RETRY] No answer from first attempt. Retrying ReAct loop.")
    ctx.retry_count += 1
    ctx.loop_termination_reason = "retry"

    retry_hint = {
        "reason": (
            "Previous attempt produced NO answer — report_completion was never called. "
            "You MUST call report_completion before your turn ends, even with a partial answer."
        ),
        "source": "retry_stage",
    }
    ctx.past_mistakes = [retry_hint] + ctx.past_mistakes[:2]

    execute_react_loop(vm, model, prompt_manager, ctx, logger)
    verify_final_answer(model, prompt_manager, ctx, logger)


def _write_run_result(ctx: PipelineContext, logger: RunLogger) -> None:
    logger.write_result({
        "final_answer": ctx.final_answer,
        "final_code": ctx.final_code,
        "verification_passed": ctx.verification_passed,
        "verification_reason": ctx.verification_reason,
        "files_used": ctx.files_used,
        "step_count": len(ctx.react_trace),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "backend": "openai_agents",
        "planning_enabled": ctx.task_plan is not None,
        "plan_complexity": ctx.task_plan.complexity if ctx.task_plan else None,
        "plan_steps_count": len(ctx.task_plan.steps) if ctx.task_plan else None,
        "validation_rejections": len(ctx.validation_log),
        "loop_termination_reason": ctx.loop_termination_reason,
        "capability_blocked": ctx.capability_blocked,
        "retry_count": ctx.retry_count,
        "trusted_rule_files_count": len(ctx.rule_graph.nodes) if ctx.rule_graph else 0,
        "mandatory_rule_paths": ctx.mandatory_rule_paths,
        "untrusted_task_file_hints": ctx.untrusted_task_file_hints,
    })
    logger.write_trace()


def run_openai_pipeline(model: str, vm, task: str, task_id: str = "", run_dir=None) -> PipelineContext:
    """Direct code pipeline for the OpenAI backend.

    The execution order is explicit:
    context -> planning -> react -> verifier -> optional retry -> result write

    Capability resolution is intentionally not regex-short-circuited before
    context loading. The model sees the trusted rule graph and repo structure
    before deciding whether the task is unsupported.
    """
    prompt_manager = PromptManager()
    ctx = PipelineContext(task=task, model=model)
    logger = RunLogger(task_id=task_id, run_dir=run_dir)

    logger.write_meta({
        "task_id": task_id,
        "model": model,
        "task": task,
        "task_fragment": task[:200],
        "started_at": datetime.now(timezone.utc).isoformat(),
        "backend": "openai_agents",
        "prompt_versions": prompt_manager.active_versions(),
    })

    execute_context_builder(vm, model, prompt_manager, ctx, logger)

    if not ctx.pipeline_complete:
        build_initial_plan(model, prompt_manager, ctx, logger)

    if not ctx.pipeline_complete:
        execute_react_loop(vm, model, prompt_manager, ctx, logger)
        verify_final_answer(model, prompt_manager, ctx, logger)
        retry_react_once(vm, model, prompt_manager, ctx, logger)

    _write_run_result(ctx, logger)
    return ctx


class Pipeline:
    """Compatibility wrapper around the direct OpenAI code pipeline.

    The OpenAI backend no longer builds a dynamic stage list; the code path is
    fixed in run_openai_pipeline() to keep execution flow obvious.
    """

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
