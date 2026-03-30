import os
import argparse
import json
import textwrap
from datetime import datetime
from pathlib import Path

from bitgn.harness_connect import HarnessServiceClientSync
from bitgn.harness_pb2 import StatusRequest, GetBenchmarkRequest, StartPlaygroundRequest, EvalPolicy, EndTrialRequest
from connectrpc.errors import ConnectError

BITGN_URL = os.getenv("BENCHMARK_HOST") or "https://api.bitgn.com"
BENCHMARK_ID = os.getenv("BENCHMARK_ID") or "bitgn/pac1-dev"

_DEFAULT_MODELS = {
    "claude": "claude-haiku-4-5-20251001",
    "openai": "gpt-5.4-nano",
}
_DEFAULT_MODEL_FALLBACK = "gpt-5.4-nano"

CLI_RED = "\x1B[31m"
CLI_GREEN = "\x1B[32m"
CLI_CLR = "\x1B[0m"

_MODEL_PRICING = {
    "gpt-5.4": {"input": 2.50, "cached_input": 0.25, "output": 15.00},
    "gpt-5.4-mini": {"input": 0.750, "cached_input": 0.075, "output": 4.500},
    "gpt-5.4-nano": {"input": 0.20, "cached_input": 0.02, "output": 1.25},
    "claude-opus-4-6": {"input": 15.00, "cached_input": 1.50, "output": 75.00},
    "claude-sonnet-4-6": {"input": 3.00, "cached_input": 0.30, "output": 15.00},
    "claude-haiku-4-5": {"input": 0.80, "cached_input": 0.08, "output": 4.00},
}


def _parse_iso(ts: str | None):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    if seconds < 60:
        return f"{seconds:.1f}s"
    mins = int(seconds // 60)
    secs = seconds - mins * 60
    return f"{mins}m {secs:.1f}s"


def _format_tokens(tokens: int | None) -> str:
    if tokens is None:
        return "-"
    return str(tokens)


def _format_price(price: float | None) -> str:
    if price is None:
        return "-"
    if price >= 0.01:
        return f"${price:.2f}"
    if price >= 0.001:
        return f"${price:.3f}"
    return f"${price:.4f}"


def _lookup_model_pricing(model_id: str) -> dict | None:
    lowered = model_id.strip().lower()
    for prefix in _MODEL_PRICING:
        if lowered.startswith(prefix):
            return _MODEL_PRICING[prefix]
    return None


def _calculate_price(model_id: str, usage: dict | None) -> float | None:
    if not usage:
        return None
    pricing = _lookup_model_pricing(model_id)
    if not pricing:
        return None

    input_tokens = int(usage.get("input_tokens", 0) or 0)
    cached_input_tokens = int(usage.get("cached_input_tokens", 0) or 0)
    cached_input_tokens += int(usage.get("cache_read_input_tokens", 0) or 0)
    output_tokens = int(usage.get("output_tokens", 0) or 0)
    uncached_input_tokens = max(0, input_tokens - cached_input_tokens)

    return (
        uncached_input_tokens / 1_000_000 * pricing["input"]
        + cached_input_tokens / 1_000_000 * pricing["cached_input"]
        + output_tokens / 1_000_000 * pricing["output"]
    )


def _load_task_summary(run_dir: Path, task_id: str, model_id: str) -> dict:
    task_dir = run_dir / "tasks" / task_id
    meta_path = task_dir / "meta.json"
    result_path = task_dir / "result.json"
    api_calls_path = task_dir / "api_calls.jsonl"

    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    result = json.loads(result_path.read_text()) if result_path.exists() else {}

    usage_totals = {
        "requests": 0,
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
    }
    has_usage = False

    if api_calls_path.exists():
        for line in api_calls_path.read_text().splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            usage = record.get("usage")
            if not isinstance(usage, dict):
                continue
            has_usage = True
            for key in usage_totals:
                usage_totals[key] += int(usage.get(key, 0) or 0)

    started_at = _parse_iso(meta.get("started_at"))
    finished_at = _parse_iso(result.get("finished_at"))
    elapsed_seconds = None
    if started_at and finished_at:
        elapsed_seconds = max(0.0, (finished_at - started_at).total_seconds())

    usage = usage_totals if has_usage else None
    price = _calculate_price(model_id, usage)

    return {
        "task_id": task_id,
        "score": result.get("benchmark_score"),
        "steps": result.get("step_count"),
        "tokens": usage["total_tokens"] if usage else None,
        "usage": usage,
        "price": price,
        "elapsed_seconds": elapsed_seconds,
    }


def _print_run_summary_table(task_summaries: list[dict], started_at: datetime) -> None:
    if not task_summaries:
        return

    headers = ("Task", "Score", "Steps", "Tokens", "Price", "Time")
    rows = []
    for item in task_summaries:
        score = item.get("score")
        score_text = f"{score:.2f}" if isinstance(score, (int, float)) else "-"
        rows.append((
            item["task_id"],
            score_text,
            str(item.get("steps", "-")),
            _format_tokens(item.get("tokens")),
            _format_price(item.get("price")),
            _format_duration(item.get("elapsed_seconds")),
        ))

    widths = [
        max(len(headers[idx]), max(len(row[idx]) for row in rows))
        for idx in range(len(headers))
    ]

    def fmt_row(row: tuple[str, ...]) -> str:
        return "  ".join(col.ljust(widths[idx]) for idx, col in enumerate(row))

    print("\n" + fmt_row(headers))
    print(fmt_row(tuple("-" * w for w in widths)))
    for row in rows:
        print(fmt_row(row))

    scored = [item["score"] for item in task_summaries if isinstance(item.get("score"), (int, float))]
    total = sum(scored) / len(scored) * 100.0 if scored else 0.0
    elapsed = max(0.0, (datetime.now() - started_at).total_seconds())
    print(f"\nFINAL: {total:0.2f}% in {_format_duration(elapsed)}")


def _make_run_dir() -> Path:
    today = datetime.now().strftime("%Y%m%d")
    base = Path("runs")
    n = len(list(base.glob(f"{today}_run*"))) + 1 if base.exists() else 1
    run_dir = base / f"{today}_run{n}"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def main() -> None:
    parser = argparse.ArgumentParser(description="Run dev agent pipeline benchmark (bitgn/pac1-dev)")
    parser.add_argument(
        "-p", "--pipeline",
        default=os.getenv("AGENT_PIPELINE_BACKEND", "claude"),
        choices=["claude", "openai"],
        help="Pipeline backend to use (default: claude, or AGENT_PIPELINE_BACKEND env var)",
    )
    parser.add_argument(
        "-m", "--model",
        default=None,
        help="Model ID override (default depends on pipeline)",
    )
    parser.add_argument(
        "tasks",
        nargs="*",
        help="Optional task IDs to run (e.g. t01 t02); runs all if omitted",
    )
    args = parser.parse_args()

    BACKEND = args.pipeline.strip().lower()
    MODEL_ID = args.model or _DEFAULT_MODELS.get(BACKEND, _DEFAULT_MODEL_FALLBACK)
    task_filter = args.tasks

    if BACKEND == "claude":
        from agent_pipeline_claude import run_agent
        from agent_pipeline_claude.infra.logger import log_benchmark_result
    elif BACKEND == "openai":
        from agent_pipeline_openai import run_agent
        from agent_pipeline_openai.infra.logger import log_benchmark_result
    else:
        raise RuntimeError(f"Unsupported --pipeline={BACKEND!r}. Expected one of: claude, openai.")

    print(f"Pipeline: {BACKEND} | Model: {MODEL_ID} | Benchmark: {BENCHMARK_ID}")

    run_dir = _make_run_dir()
    run_started_at = datetime.now()
    scores = []
    task_summaries: list[dict] = []
    try:
        client = HarnessServiceClientSync(BITGN_URL)
        print("Connecting to BitGN", client.status(StatusRequest()))
        res = client.get_benchmark(GetBenchmarkRequest(benchmark_id=BENCHMARK_ID))
        print(
            f"{EvalPolicy.Name(res.policy)} benchmark: {res.benchmark_id} "
            f"with {len(res.tasks)} tasks.\n{CLI_GREEN}{res.description}{CLI_CLR}"
        )

        for t in res.tasks:
            if task_filter and t.task_id not in task_filter:
                continue
            print("=" * 40)
            print(f"Starting Task: {t.task_id}")

            trial = client.start_playground(StartPlaygroundRequest(
                benchmark_id=BENCHMARK_ID,
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

            task_summary = _load_task_summary(run_dir, t.task_id, MODEL_ID)
            task_summaries.append(task_summary)

            if result.score >= 0:
                scores.append((t.task_id, result.score))
                style = CLI_GREEN if result.score == 1 else CLI_RED
                explain = textwrap.indent("\n".join(result.score_detail), "  ")
                print(f"\n{style}Score: {result.score:0.2f}\n{explain}\n{CLI_CLR}")
                usage = task_summary.get("usage") or {}
                tokens = task_summary.get("tokens")
                price = task_summary.get("price")
                print(
                    f"Usage: input={usage.get('input_tokens', 0)} "
                    f"cached={usage.get('cached_input_tokens', 0)} "
                    f"output={usage.get('output_tokens', 0)} "
                    f"total={tokens or 0} | Price: {_format_price(price)}"
                )

    except ConnectError as e:
        print(f"{e.code}: {e.message}")
    except KeyboardInterrupt:
        print(f"{CLI_RED}Interrupted{CLI_CLR}")

    if task_summaries:
        _print_run_summary_table(task_summaries, run_started_at)


if __name__ == "__main__":
    main()
