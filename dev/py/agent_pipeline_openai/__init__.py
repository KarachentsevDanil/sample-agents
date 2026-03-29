from dotenv import load_dotenv

from bitgn.vm.pcm_connect import PcmRuntimeClientSync

from .pipeline import run_openai_pipeline

__all__ = ["run_agent"]


def configure_openai_agents_sdk() -> None:
    load_dotenv()
    try:
        from agents.models.openai_responses import OpenAIResponsesModel  # noqa: F401
    except ImportError:
        pass


def run_agent(model: str, harness_url: str, task_text: str,
              task_id: str = "", run_dir=None) -> str:
    configure_openai_agents_sdk()
    vm = PcmRuntimeClientSync(harness_url)
    ctx = run_openai_pipeline(
        model=model,
        vm=vm,
        task=task_text,
        task_id=task_id,
        run_dir=run_dir,
    )
    print(f"Result: {ctx.final_code} — {ctx.final_answer[:120]}")
    return ctx.final_answer
