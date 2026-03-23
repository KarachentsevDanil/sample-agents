from bitgn.vm.mini_connect import MiniRuntimeClientSync

from .pipeline import Pipeline
from openai_config import configure_openai_agents_sdk

CLI_GREEN = "\x1B[32m"
CLI_CLR = "\x1B[0m"


def run_agent(model: str, harness_url: str, task_text: str,
              task_id: str = "", run_dir=None) -> str:
    configure_openai_agents_sdk()
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
