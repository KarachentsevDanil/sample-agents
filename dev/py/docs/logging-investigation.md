# Logging Investigation — Replicating the ERC3 Trace Visualizer

**Reference**: https://ilyarice.github.io/Enterprise-RAG-Challenge-3-AI-Agents/
**Source repo**: https://github.com/IlyaRice/Enterprise-RAG-Challenge-3-AI-Agents
**Analysis date**: 2026-03-25

---

## What the Reference Visualizer Does

The reference is a React 19 + D3.js SPA with a three-panel layout:

| Panel | Content |
|-------|---------|
| Left sidebar | Task list: index, truncated text, status icon, score badge (green ≥0.8, amber ≥0.5, red <0.5) |
| Center | Interactive D3 tree — nodes as colored circles, hierarchical (dot-notation IDs), zoom/pan, hover tooltips |
| Right panel | Selected node details: system prompt, conversation messages, thinking/reasoning, tool call request+response, validator results |

Key properties:
- Nodes are colored by **context type** (Orchestrator, TaskAnalyzer, ProductExplorer…)
- Parent–child relationships are explicit (node IDs like `1`, `2.1`, `2.3.1`)
- Every agent step shows: full conversation history at that point, parsed LLM output, tool calls with request/response pairs, thinking blocks
- Validator events have their own tab (validator prompt, reasoning, `validation_passed: bool`)

---

## Reference Data Schema

### TraceEvent (core unit)

```json
{
  "type": "agent_step" | "validator_step",
  "node_id": "2.3",
  "parent_node_id": "2",
  "prev_sibling_node_id": "2.2",
  "depth": 2,
  "ts": 1774338585.289,
  "context": "TaskAnalyzer",
  "system_prompt": "...",
  "messages": [{"role": "user", "content": "..."}, ...],
  "thinking": "I need to understand the task...",
  "output": {"type": "tool_use", "tool_name": "read", "args": {"path": "..."}},
  "tool_calls": [{"name": "read", "request": {...}, "response": {...}}],
  "subagent_results": [],
  "validation_passed": null
}
```

### TaskResult

```json
{
  "task_id": "t01",
  "task_index": 0,
  "task_text": "Remove all captured cards and threads",
  "code": "OUTCOME_OK",
  "summary": "Done. Removed 5 cards and 2 threads.",
  "score": 1.0,
  "eval_logs": "...",
  "trace": [TraceEvent, ...]
}
```

### RunMeta

```json
{
  "benchmark": "erc3-dev",
  "start_time": "2026-03-24T07:49:40Z",
  "session_id": "vm-02ka3at5k644cy1",
  "task_count": 50,
  "total_score": 42.5,
  "architecture": "Multiagent"
}
```

---

## Our Current Logging State

### What exists

Files written per task to `runs/{date}_run1/tasks/{task_id}/`:

| File | Content | Written by |
|------|---------|-----------|
| `meta.json` | task_id, model, started_at, prompt_versions | `pipeline.py:write_meta()` |
| `api_calls.jsonl` | One LLM call per line: stage, model, system, messages, response, usage | `react.py:logger.append_api_call()` |
| `react_trace.jsonl` | One tool step per line: step, cmd, args, result (truncated 400 chars), ts | `react.py:logger.append_react_step()` |
| `llm_parse_errors.jsonl` | Parsing failures | on error |
| `result.json` | final_answer, final_code, verification_passed, files_used, step_count, finished_at | `pipeline.py:write_result()` |
| `mistakes/errors.jsonl` | Learning log | on benchmark failure |

### Gap Analysis vs. Reference

| Feature | Reference | Ours | Gap |
|---------|-----------|------|-----|
| Hierarchical node IDs | `1`, `2.1`, `2.3.1` | Flat step counter | Missing |
| Parent–child links | `parent_node_id` field | None | Missing |
| Execution context type | `"context": "TaskAnalyzer"` | Not captured | Missing |
| Thinking/reasoning | `thinking` field per step | Not logged (exists in API response) | Easy add |
| Tool call request+response pair | Structured `tool_calls[]` | Split across two records | Needs merge |
| Validator events | Separate `validator_step` type | Only final verify in result.json | Missing |
| Session-level metadata | `RunMeta` (session_id, total_score) | Per-task only | Easy add |
| Interactive visualization | React SPA | None | Build needed |
| Result truncation | Full tool response | 400 chars max | Should lift limit |

---

## Replication Plan

### Phase 1 — Extend the log schema (Medium, ~2–3h)

**Goal**: capture the fields needed for visualization without breaking existing consumers.

1. **Add `thinking` capture** — Claude's extended thinking blocks are already in `response.content`. Extract them and add to the api_call log entry:

```python
# In agent_pipeline_claude/_logging.py
def build_api_log_entry(stage, model, system, messages, response, **extra):
    thinking = ""
    for block in response.content:
        if hasattr(block, "type") and block.type == "thinking":
            thinking = getattr(block, "thinking", "")
            break
    return {
        "stage": stage,
        "ts": time.time(),
        "model": model,
        "system": system,
        "messages": messages,
        "thinking": thinking,   # NEW
        "response_stop_reason": response.stop_reason,
        "response_content": [b.model_dump() for b in response.content],
        "usage": response.usage.model_dump(),
        **extra,
    }
```

2. **Add node ID tracking** — add `node_id` to `PipelineContext` and increment as a hierarchical counter. For a single-agent pipeline, flat IDs (`"1"`, `"2"`, ...) are sufficient now; hierarchical IDs (`"2.1"`) become relevant when sub-agents are introduced.

```python
# In agent_pipeline_claude/models.py
@dataclass
class PipelineContext:
    ...
    node_counter: int = 0   # NEW: bump before each LLM call
```

3. **Lift the 400-char truncation** on tool results in react_trace. The visualization needs the full response to show in the detail panel. Store full result in a separate `tool_results.jsonl` if size is a concern, but keep it accessible.

4. **Link tool call request+response** — the current split (args in react_trace, full response in api_calls) makes reconstruction hard. Add a `tool_calls` field to each LLM step log that pairs them:

```python
step_record = {
    "step": step_count,
    "node_id": f"{ctx.node_counter}",   # NEW
    "cmd": tool_name,
    "args": tool_input,
    "result": result_text,              # full, not truncated
    "ts": time.time(),
}
```

---

### Phase 2 — Export trace.json aggregation (Medium, ~2–3h)

**Goal**: produce a single `trace.json` per task that the visualizer can load, while keeping existing JSONL files.

```python
# In agent_pipeline_claude/logger.py
def export_trace(self) -> dict:
    """Reconstruct hierarchical trace from JSONL files."""
    meta = json.loads((self._task_dir / "meta.json").read_text())
    result = json.loads((self._task_dir / "result.json").read_text())

    api_calls = [json.loads(l) for l in (self._task_dir / "api_calls.jsonl").read_text().splitlines() if l]
    react_steps = [json.loads(l) for l in (self._task_dir / "react_trace.jsonl").read_text().splitlines() if l]

    # Build TraceEvent per LLM call
    trace = []
    for i, call in enumerate(api_calls):
        # Find the tool step(s) that followed this LLM call
        step_no = call.get("step", i)
        tool_calls = [s for s in react_steps if s.get("step") == step_no]

        trace.append({
            "type": "agent_step",
            "node_id": str(i + 1),
            "parent_node_id": None,
            "depth": 0,
            "ts": call["ts"],
            "context": "ReActAgent",
            "system_prompt": call.get("system", ""),
            "messages": call.get("messages", []),
            "thinking": call.get("thinking", ""),
            "output": {"type": "tool_use", "tool_name": tool_calls[0]["cmd"] if tool_calls else None},
            "tool_calls": [
                {"name": s["cmd"], "request": s["args"], "response": s["result"]}
                for s in tool_calls
            ],
        })

    return {
        "meta": {**meta, "benchmark": "pac1"},
        "task": {
            "text": meta.get("task_text", ""),
            "outcome": result.get("final_code"),
            "final_answer": result.get("final_answer"),
            "score": None,   # filled by benchmark runner
            "files_used": result.get("files_used", []),
            "step_count": result.get("step_count"),
        },
        "trace": trace,
    }

def write_trace(self) -> None:
    """Call after task completes to produce trace.json."""
    trace_data = self.export_trace()
    (self._task_dir / "trace.json").write_text(json.dumps(trace_data, indent=2))
```

Call `logger.write_trace()` at the end of `pipeline.py` after `write_result()`.

---

### Phase 3 — Build the HTML visualizer (Medium-High, ~8–12h)

**Recommended approach**: static self-contained HTML generator (MVP). Full React SPA can come later if warranted.

#### Option A: Static HTML (Recommended for MVP)

Generate `trace.html` that embeds trace.json inline. Uses D3.js + Tailwind CDN — no build step, no server.

```python
# In agent_pipeline_claude/logger.py
TRACE_HTML_TEMPLATE = """<!DOCTYPE html>
<html class="dark">
<head>
  <meta charset="UTF-8">
  <title>Trace: {task_id}</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script src="https://d3js.org/d3.v7.min.js"></script>
</head>
<body class="bg-neutral-950 text-neutral-100 h-screen flex">
  <!-- Left: task summary -->
  <div id="sidebar" class="w-64 border-r border-neutral-800 p-4 overflow-y-auto"></div>
  <!-- Center: D3 tree -->
  <div id="tree" class="flex-1 relative"><svg id="treeSvg" class="w-full h-full"></svg></div>
  <!-- Right: detail panel -->
  <div id="detail" class="w-96 border-l border-neutral-800 p-4 overflow-y-auto hidden"></div>
  <script>
    const TRACE = {trace_json};
    // 1. Render sidebar
    // 2. Build D3 hierarchy from TRACE.trace (node_id + parent_node_id)
    // 3. Diagonal cascade layout
    // 4. Click node → show detail panel
    // 5. Detail panel: system_prompt, messages, thinking, tool_calls
  </script>
</body>
</html>"""

def write_trace_html(self) -> None:
    trace_data = self.export_trace()
    html = TRACE_HTML_TEMPLATE.format(
        task_id=trace_data["meta"].get("task_id", "unknown"),
        trace_json=json.dumps(trace_data),
    )
    (self._task_dir / "trace.html").write_text(html)
```

#### Option B: Port the reference React SPA

- Set up Vite + React + TypeScript project under `tools/trace-viewer/`
- Port the 5 reference components: App, TaskSidebar, TreeVisualizer, DetailPanel, RunHeader
- Load `trace.json` from disk instead of embedded demo files
- Build produces a single `dist/index.html` — copy alongside task run

**Decision criteria**: go Option B when you need multi-task comparison (view 50 tasks in one session). For single-task debugging, Option A is sufficient.

---

### Complexity and Effort Summary

| Phase | Description | Effort | Risk |
|-------|-------------|--------|------|
| 1 | Extend log schema (thinking, node_id, full results) | 2–3h | Low |
| 2 | trace.json aggregation export | 2–3h | Low |
| 3a | Static HTML generator (MVP) | 4–6h | Low |
| 3b | Full React SPA port | 20–30h | Medium |
| **Total MVP** | **Phases 1 + 2 + 3a** | **~10–12h** | **Low** |

---

## Recommended Log Schema (target state)

`trace.json` per task (written alongside existing JSONL files):

```json
{
  "meta": {
    "task_id": "t01",
    "task_index": 0,
    "model": "claude-sonnet-4-6",
    "started_at": "2026-03-24T07:49:40Z",
    "finished_at": "2026-03-24T07:50:02Z",
    "prompt_versions": {"system": "v7"},
    "session_id": "vm-02ka3at5k644cy1",
    "benchmark": "pac1-dev"
  },
  "task": {
    "text": "Remove all captured cards and threads",
    "outcome": "OUTCOME_OK",
    "final_answer": "Done. Removed 5 cards and 2 threads.",
    "score": 1.0,
    "files_used": ["90_memory/Soul.md"],
    "step_count": 11
  },
  "trace": [
    {
      "type": "agent_step",
      "node_id": "1",
      "parent_node_id": null,
      "depth": 0,
      "ts": 1774338585.289,
      "context": "ReActAgent",
      "system_prompt": "You are a helpful assistant...",
      "messages": [{"role": "user", "content": "Task: ..."}],
      "thinking": "I need to explore the filesystem first.",
      "output": {"type": "tool_use", "tool_name": "tree", "args": {"root": "", "level": 2}},
      "tool_calls": [
        {"name": "tree", "request": {"root": "", "level": 2}, "response": {"entries": [...]}}
      ]
    }
  ]
}
```

---

## Implementation Order

1. **Phase 1 first** — schema changes are prerequisite for everything else. Start with `thinking` extraction (5 lines) and removing the 400-char truncation.
2. **Phase 2 second** — `export_trace()` and `write_trace()` in logger. Call from pipeline.py.
3. **Phase 3a** — Static HTML generator. Build incrementally: tree layout first, then detail panel.
4. **Validate** — run a task, open `trace.html`, compare layout against https://ilyarice.github.io/Enterprise-RAG-Challenge-3-AI-Agents/
5. **Phase 3b (optional)** — port React SPA only if multi-task view or FileBrowser is needed.
