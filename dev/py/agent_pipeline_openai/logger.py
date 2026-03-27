import json
import uuid
from datetime import datetime, timezone
from pathlib import Path


class RunLogger:
    def __init__(self, task_id: str, run_dir=None, benchmark: str = "pac1"):
        if run_dir is not None:
            self._task_dir = Path(run_dir) / "tasks" / (task_id or "unknown")
        else:
            today = datetime.now(timezone.utc).strftime("%Y%m%d")
            self._task_dir = Path("runs") / f"{today}_run1" / "tasks" / (task_id or "unknown")
        self._task_dir.mkdir(parents=True, exist_ok=True)
        self._mistakes_path = Path("mistakes") / (task_id or "unknown") / "errors.jsonl"
        self._session_id = str(uuid.uuid4())
        self._finished_at: str = ""
        self._benchmark = benchmark

    def write_meta(self, meta: dict) -> None:
        meta = {**meta, "session_id": self._session_id}
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
        self._finished_at = datetime.utcnow().isoformat() + "Z"
        (self._task_dir / "result.json").write_text(json.dumps(result, indent=2))

    def load_past_mistakes(self) -> list:
        if not self._mistakes_path.exists():
            return []
        lines = self._mistakes_path.read_text().splitlines()
        return [json.loads(line) for line in lines if line.strip()]

    def export_trace(self) -> dict:
        """Reconstruct hierarchical trace from JSONL files."""
        meta_path = self._task_dir / "meta.json"
        result_path = self._task_dir / "result.json"
        api_path = self._task_dir / "api_calls.jsonl"
        react_path = self._task_dir / "react_trace.jsonl"

        meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
        result = json.loads(result_path.read_text()) if result_path.exists() else {}

        api_calls = []
        if api_path.exists():
            api_calls = [json.loads(l) for l in api_path.read_text().splitlines() if l.strip()]

        react_steps = []
        if react_path.exists():
            react_steps = [json.loads(l) for l in react_path.read_text().splitlines() if l.strip()]

        # Index react steps by node_id for fast lookup
        steps_by_node = {}
        for s in react_steps:
            nid = s.get("node_id")
            if nid:
                steps_by_node.setdefault(nid, []).append(s)

        trace = []

        # Build trace events from api_calls (LLM calls)
        for call in api_calls:
            node_id = call.get("node_id", str(call.get("step", 0)))
            matched_steps = steps_by_node.get(node_id, [])

            # Skip entries that are tool-level records (OpenAI pipeline writes
            # tool results to api_calls.jsonl with a "cmd" field)
            if "cmd" in call and "stage" not in call:
                continue

            trace.append({
                "type": "agent_step",
                "node_id": node_id,
                "parent_node_id": None,
                "depth": 0,
                "ts": call.get("ts", 0),
                "context": "ReActAgent",
                "system_prompt": call.get("system", ""),
                "messages": call.get("messages", []),
                "thinking": call.get("thinking", ""),
                "output": {
                    "type": "tool_use",
                    "tool_name": matched_steps[0]["cmd"] if matched_steps else None,
                    "args": matched_steps[0].get("args") if matched_steps else None,
                },
                "tool_calls": [
                    {"name": s["cmd"], "request": s.get("args", {}), "response": s.get("result", "")}
                    for s in matched_steps if s.get("type") != "validator_step"
                ],
            })

        # Append validator steps from react_trace
        for s in react_steps:
            if s.get("type") == "validator_step":
                trace.append({
                    "type": "validator_step",
                    "node_id": s.get("node_id", ""),
                    "parent_node_id": None,
                    "depth": 0,
                    "ts": s.get("ts", 0),
                    "context": "Verifier",
                    "validation_passed": s.get("validation_passed", None),
                    "result": s.get("result", ""),
                })

        return {
            "meta": {**meta, "benchmark": self._benchmark, "session_id": self._session_id, "finished_at": self._finished_at},
            "task": {
                "text": meta.get("task", ""),
                "outcome": result.get("final_code"),
                "final_answer": result.get("final_answer"),
                "score": result.get("benchmark_score"),
                "files_used": result.get("files_used", []),
                "step_count": result.get("step_count"),
            },
            "trace": trace,
        }

    def write_trace(self) -> None:
        """Produce trace.json and trace.html after task completes."""
        trace_data = self.export_trace()
        (self._task_dir / "trace.json").write_text(json.dumps(trace_data, indent=2))
        self._write_trace_html(trace_data)

    def _write_trace_html(self, trace_data: dict) -> None:
        """Generate self-contained trace.html with embedded trace data."""
        template_path = Path(__file__).resolve().parent.parent / "trace_viewer.html"
        if not template_path.exists():
            return
        template = template_path.read_text()
        # Inject trace data by replacing the placeholder
        trace_json = json.dumps(trace_data)
        html = template.replace(
            '/*TRACE_JSON_PLACEHOLDER*/ null',
            trace_json,
        )
        (self._task_dir / "trace.html").write_text(html)

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
