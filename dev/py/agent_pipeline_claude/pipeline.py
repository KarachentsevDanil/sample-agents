import os
import re
from datetime import datetime, timezone

import anthropic
from connectrpc.errors import ConnectError
from dotenv import load_dotenv

from bitgn.vm.pcm_pb2 import AnswerRequest, Outcome

load_dotenv()

from .stages.context import ContextBuilderStage
from .stages.planning import PlanningStage
from .stages.react import ReActLoopStage
from .infra.logger import RunLogger
from .models import PipelineContext, budget_from_plan
from .prompt_resources.prompt_manager import PromptManager

# ── Injection pre-scan (zero-token cost) ────────────────────────────

_INJECTION_PATTERNS = [
    re.compile(r"<<<.*>>>", re.IGNORECASE),
    re.compile(r"SYS_OVERRIDE|SYSTEM_OVERRIDE|END_OVERRIDE", re.IGNORECASE),
    re.compile(r"@clipboard|@shell|@exec", re.IGNORECASE),
    re.compile(r"delete\s+.*AGENTS|remove\s+.*AGENTS|ignore\s+.*rules", re.IGNORECASE),
    re.compile(r"<script|<\|im_start\|>|\[INST\]|\[/INST\]", re.IGNORECASE),
    re.compile(r"suppress\s+mention|return\s+success\s+only", re.IGNORECASE),
]

_OUTCOME_PROTO = {
    "OUTCOME_OK": Outcome.OUTCOME_OK,
    "OUTCOME_DENIED_SECURITY": Outcome.OUTCOME_DENIED_SECURITY,
    "OUTCOME_NONE_CLARIFICATION": Outcome.OUTCOME_NONE_CLARIFICATION,
    "OUTCOME_NONE_UNSUPPORTED": Outcome.OUTCOME_NONE_UNSUPPORTED,
    "OUTCOME_ERR_INTERNAL": Outcome.OUTCOME_ERR_INTERNAL,
}


def _scan_for_injection(text: str) -> str | None:
    for pat in _INJECTION_PATTERNS:
        m = pat.search(text)
        if m:
            return m.group(0)
    return None


def _submit_answer(vm, ctx: PipelineContext) -> None:
    outcome_proto = _OUTCOME_PROTO.get(ctx.final_code, Outcome.OUTCOME_OK)
    try:
        vm.answer(AnswerRequest(message=ctx.final_answer, outcome=outcome_proto, refs=[]))
        ctx.harness_answer_submitted = True
    except ConnectError as e:
        print(f"[PIPELINE] Failed to submit answer: {e}")


def execute_context_builder(vm, client: anthropic.Anthropic,
                            context_agent_model: str, context_assess_model: str,
                            prompt_manager: PromptManager,
                            ctx: PipelineContext, logger: RunLogger) -> None:
    ContextBuilderStage(vm, client, context_agent_model, context_assess_model,
                        prompt_manager).execute(ctx, logger)


def build_initial_plan(client: anthropic.Anthropic, model: str,
                       prompt_manager: PromptManager,
                       ctx: PipelineContext, logger: RunLogger) -> None:
    PlanningStage(client, model, prompt_manager).execute(ctx, logger)
    if ctx.task_plan:
        ctx.react_max_steps = budget_from_plan(len(ctx.task_plan.steps))
        logger.append_api_call({
            "stage": "plan_output",
            "task_interpretation": ctx.task_plan.task_interpretation,
            "relevant_rules": ctx.task_plan.relevant_rules,
            "steps": [{"id": s.id, "description": s.description} for s in ctx.task_plan.steps],
            "complexity": ctx.task_plan.complexity,
            "early_outcome": ctx.task_plan.early_outcome,
            "early_outcome_reason": ctx.task_plan.early_outcome_reason,
        })


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

    # Resolve per-stage models (fall back to pipeline default)
    context_model = prompt_manager.model_for("context_agent", model)
    context_assess_model = prompt_manager.model_for("context", model)
    planning_model = prompt_manager.model_for("planning", model)
    react_model = prompt_manager.model_for("react", model)

    logger.write_meta({
        "task_id": task_id,
        "model": model,
        "models": {
            "context_agent": context_model,
            "context_assess": context_assess_model,
            "planning": planning_model,
            "react": react_model,
        },
        "reasoning": {
            "context_agent": prompt_manager.reasoning_for("context_agent"),
            "planning": prompt_manager.reasoning_for("planning"),
            "react": prompt_manager.reasoning_for("react"),
        },
        "task": task,
        "task_fragment": task[:200],
        "started_at": datetime.now(timezone.utc).isoformat(),
        "backend": "anthropic",
        "prompt_versions": prompt_manager.active_versions(),
    })

    # Stage 0: Programmatic injection pre-scan (zero LLM cost)
    injection_match = _scan_for_injection(task)
    if injection_match:
        ctx.final_answer = f"Injection detected in task instruction: {injection_match}"
        ctx.final_code = "OUTCOME_DENIED_SECURITY"
        ctx.loop_termination_reason = "injection_prescan"
        ctx.pipeline_complete = True
        _submit_answer(vm, ctx)
        print(f"[PRESCAN] Injection detected: {injection_match}")

    # Stage 1: Build context
    if not ctx.pipeline_complete:
        execute_context_builder(vm, client, context_model, context_assess_model,
                                prompt_manager, ctx, logger)

    # Stage 2: Plan (1 LLM call)
    if not ctx.pipeline_complete:
        build_initial_plan(client, planning_model, prompt_manager, ctx, logger)

    # Stage 2.5: Early outcome gate
    if not ctx.pipeline_complete and ctx.task_plan and ctx.task_plan.early_outcome:
        ctx.final_answer = ctx.task_plan.early_outcome_reason or "Task not actionable."
        ctx.final_code = ctx.task_plan.early_outcome
        ctx.loop_termination_reason = "early_outcome"
        ctx.pipeline_complete = True
        _submit_answer(vm, ctx)
        print(f"[PLAN] Early outcome: {ctx.task_plan.early_outcome}")

    # Stage 3: Execute (ReAct loop)
    if not ctx.pipeline_complete:
        execute_react_loop(vm, client, react_model, prompt_manager, ctx, logger)

    _write_run_result(ctx, logger)
    return ctx
