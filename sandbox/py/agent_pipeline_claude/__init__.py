from bitgn.vm.mini_connect import MiniRuntimeClientSync

from .pipeline import Pipeline
from ._cli import CLI_GREEN, CLI_CLR

__all__ = ["run_agent"]


def run_agent(model: str, harness_url: str, task_text: str,
              task_id: str = "", run_dir=None) -> str:
    vm = MiniRuntimeClientSync(harness_url)
    ctx = (
        Pipeline(model, vm, task_text, task_id=task_id, run_dir=run_dir)
        .use_context()
        .use_react()
        .use_response_verifier()
        .run()
    )
    print(f"Verification: {ctx.verification_passed} — {ctx.verification_reason}")
    return ctx.final_answer
