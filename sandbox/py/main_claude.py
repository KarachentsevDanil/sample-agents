import os
import textwrap
from datetime import datetime
from pathlib import Path

from bitgn.harness_connect import HarnessServiceClientSync
from bitgn.harness_pb2 import StatusRequest, GetBenchmarkRequest, StartPlaygroundRequest, EvalPolicy, EndTrialRequest
from connectrpc.errors import ConnectError

from agent_pipeline_claude import run_agent
from agent_pipeline_claude.logger import log_benchmark_result

BITGN_URL = os.getenv("BENCHMARK_HOST") or "https://api.bitgn.com"

MODEL_ID = "claude-haiku-4-5"

CLI_RED = "\x1B[31m"
CLI_GREEN = "\x1B[32m"
CLI_CLR = "\x1B[0m"


def _make_run_dir() -> Path:
    today = datetime.now().strftime("%Y%m%d")
    base = Path("claude_runs")
    n = len(list(base.glob(f"{today}_run*"))) + 1 if base.exists() else 1
    run_dir = base / f"{today}_run{n}"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def main() -> None:
    task_filter = os.sys.argv[1:]

    run_dir = _make_run_dir()
    scores = []
    try:
        print("Connecting")
        client = HarnessServiceClientSync(BITGN_URL)
        print("Connecting to BitGN", client.status(StatusRequest()))
        res = client.get_benchmark(GetBenchmarkRequest(benchmark_id="bitgn/sandbox"))
        print(f"{EvalPolicy.Name(res.policy)} benchmark: {res.benchmark_id} with {len(res.tasks)} tasks.\n{CLI_GREEN}{res.description}{CLI_CLR}")

        for t in res.tasks:
            if task_filter and t.task_id not in task_filter:
                continue
            print("=" * 40)
            print(f"Starting Task: {t.task_id}")

            trial = client.start_playground(StartPlaygroundRequest(
                benchmark_id="bitgn/sandbox",
                task_id=t.task_id,
            ))

            print("Task:", trial.instruction)

            agent_answer = ""
            try:
                agent_answer = run_agent(MODEL_ID, trial.harness_url, trial.instruction,
                                         task_id=t.task_id, run_dir=run_dir)
            except Exception as e:
                print(e)

            result = client.end_trial(EndTrialRequest(trial_id=trial.trial_id))
            log_benchmark_result(
                run_dir, t.task_id,
                score=result.score,
                score_detail=list(result.score_detail),
                trial_id=result.trial_id,
                agent_answer=agent_answer,
            )

            if result.score >= 0:
                scores.append((t.task_id, result.score))

                style = CLI_GREEN if result.score == 1 else CLI_RED

                explain = textwrap.indent("\n".join(result.score_detail), "  ")
                print(f"\n{style}Score: {result.score:0.2f}\n{explain}\n{CLI_CLR}")

    except ConnectError as e:
        print(f"{e.code}: {e.message}")
    except KeyboardInterrupt:
        print(f"{CLI_RED}Interrupted{CLI_CLR}")

    if scores:
        for tid, score in scores:
            style = CLI_GREEN if score == 1 else CLI_RED
            print(f"{tid}: {style}{score:0.2f}{CLI_CLR}")

        total = sum([t[1] for t in scores]) / len(scores) * 100.0
        print(f"FINAL: {total:0.2f}%")


if __name__ == "__main__":
    main()
