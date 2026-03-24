import json
from datetime import datetime, timezone
from pathlib import Path


class RunLogger:
    def __init__(self, task_id: str, run_dir=None):
        if run_dir is not None:
            self._task_dir = Path(run_dir) / "tasks" / (task_id or "unknown")
        else:
            today = datetime.now(timezone.utc).strftime("%Y%m%d")
            self._task_dir = Path("runs") / f"{today}_run1" / "tasks" / (task_id or "unknown")
        self._task_dir.mkdir(parents=True, exist_ok=True)
        self._mistakes_path = Path("mistakes") / (task_id or "unknown") / "errors.jsonl"

    def write_meta(self, meta: dict) -> None:
        (self._task_dir / "meta.json").write_text(json.dumps(meta, indent=2))

    def append_api_call(self, record: dict) -> None:
        with (self._task_dir / "api_calls.jsonl").open("a") as f:
            f.write(json.dumps(record) + "\n")

    def append_react_step(self, record: dict) -> None:
        with (self._task_dir / "react_trace.jsonl").open("a") as f:
            f.write(json.dumps(record) + "\n")

    def append_llm_parse_error(self, record: dict) -> None:
        with (self._task_dir / "llm_parse_errors.jsonl").open("a") as f:
            f.write(json.dumps(record) + "\n")

    def write_result(self, result: dict) -> None:
        (self._task_dir / "result.json").write_text(json.dumps(result, indent=2))

    def load_past_mistakes(self) -> list:
        if not self._mistakes_path.exists():
            return []
        lines = self._mistakes_path.read_text().splitlines()
        return [json.loads(line) for line in lines if line.strip()]

    def append_mistake(self, record: dict) -> None:
        self._mistakes_path.parent.mkdir(parents=True, exist_ok=True)
        existing = self.load_past_mistakes()
        if any(m.get("reason") == record.get("reason") for m in existing):
            return
        with self._mistakes_path.open("a") as f:
            f.write(json.dumps(record) + "\n")


def log_benchmark_result(
    run_dir,
    task_id: str,
    score: float,
    score_detail: list,
    trial_id: str = "",
    agent_answer: str = "",
) -> None:
    if run_dir:
        result_path = Path(run_dir) / "tasks" / task_id / "result.json"
        data = json.loads(result_path.read_text()) if result_path.exists() else {}
        data["benchmark_score"] = score
        data["benchmark_score_detail"] = score_detail
        data["trial_id"] = trial_id
        result_path.write_text(json.dumps(data, indent=2))

    if score < 1.0:
        mistakes_path = Path("mistakes") / task_id / "errors.jsonl"
        reason = score_detail[0] if score_detail else f"score={score:.2f}"
        record = {
            "source": "benchmark",
            "trial_id": trial_id,
            "score": score,
            "score_detail": score_detail,
            "agent_answer": agent_answer,
            "reason": reason,
        }
        mistakes_path.parent.mkdir(parents=True, exist_ok=True)
        existing = []
        if mistakes_path.exists():
            existing = [json.loads(l) for l in mistakes_path.read_text().splitlines() if l.strip()]
        if not any(m.get("reason") == reason for m in existing):
            with mistakes_path.open("a") as f:
                f.write(json.dumps(record) + "\n")
