from datetime import datetime, timezone

from .context import ContextBuilderStage
from .logger import RunLogger
from .models import PipelineContext
from .react import ReActLoopStage
from .verifier import VerifierStage


class Pipeline:
    def __init__(self, model: str, vm, task: str,
                 task_id: str = "", run_dir=None):
        self._model = model
        self._vm = vm
        self._task = task
        self._task_id = task_id
        self._run_dir = run_dir
        self._stages: list = []

    def use_context(self) -> "Pipeline":
        self._stages.append(ContextBuilderStage(self._vm, self._model))
        return self

    def use_react(self) -> "Pipeline":
        self._stages.append(ReActLoopStage(self._vm, self._model))
        return self

    def use_response_verifier(self) -> "Pipeline":
        self._stages.append(VerifierStage(self._model))
        return self

    def run(self) -> PipelineContext:
        ctx = PipelineContext(task=self._task, model=self._model)
        logger = RunLogger(task_id=self._task_id, run_dir=self._run_dir)

        logger.write_meta({
            "task_id": self._task_id,
            "model": self._model,
            "task_fragment": self._task[:200],
            "started_at": datetime.now(timezone.utc).isoformat(),
            "backend": "openai_agents",
        })

        for stage in self._stages:
            stage.execute(ctx, logger)

        logger.write_result({
            "final_answer": ctx.final_answer,
            "final_code": ctx.final_code,
            "verification_passed": ctx.verification_passed,
            "verification_reason": ctx.verification_reason,
            "files_used": ctx.files_used,
            "step_count": len(ctx.react_trace),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "backend": "openai_agents",
        })

        return ctx
