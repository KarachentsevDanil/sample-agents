from bitgn.vm.pcm_connect import PcmRuntimeClientSync

from .pipeline import run_claude_pipeline

__all__ = ["run_agent"]


def run_agent(model: str, harness_url: str, task_text: str,
              task_id: str = "", run_dir=None) -> str:
    vm = PcmRuntimeClientSync(harness_url)
    ctx = run_claude_pipeline(
        model=model,
        vm=vm,
        task=task_text,
        task_id=task_id,
        run_dir=run_dir,
    )
    print(f"Result: {ctx.final_code} — {ctx.final_answer[:120]}")
    return ctx.final_answer
