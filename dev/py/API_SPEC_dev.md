# BitGN Debug Proxy — Dev (pac-dev) API Specification

## Overview

A Flask server that proxies the BitGN harness and PCM VM runtime gRPC services over REST+JSON and exposes a Swagger UI for interactive debugging. Maintains an in-memory registry of active trials so VM calls only need a `trial_id` — the `harness_url` is looked up automatically.

Benchmark: `bitgn/pac1-dev`

---

## Architecture

```
Browser / Swagger UI
        │
        ▼
  Flask app_dev.py  (port 8081)
        │
   ┌────┴────────────────────────────────────┐
   │                                         │
   ▼                                         ▼
HarnessServiceClientSync               PcmRuntimeClientSync
(api.bitgn.com)                        (per-trial harness_url)
```

**State (in-memory dict):**
```python
active_trials: dict[str, str]  # trial_id → harness_url
```

---

## Dependencies

```
flask
flasgger                 # generates OpenAPI spec from docstrings
flask-cors               # allow browser requests from any origin
bitgn-api-connectrpc-python   # PCM runtime SDK (from buf.build)
bitgn-api-protocolbuffers-python
```

---

## Configuration

| Env var | Default | Description |
|---------|---------|-------------|
| `BENCHMARK_HOST` | `https://api.bitgn.com` | BitGN harness URL |
| `PORT` | `8081` | Flask listen port |

---

## Endpoints

### Harness Group — `/harness`

---

#### `GET /harness/status`

**Purpose:** Check connectivity to the BitGN harness.

**Response 200:**
```json
{
  "status": "ok",
  "version": "1.2.3"
}
```

---

#### `GET /harness/benchmark/{benchmark_id}`

**Purpose:** Get benchmark metadata and full task list.

**Path params:**
- `benchmark_id` (string, required) — e.g. `bitgn/pac1-dev`

**Response 200:**
```json
{
  "benchmark_id": "bitgn/pac1-dev",
  "description": "PAC1 dev benchmark",
  "harness_id": "...",
  "policy": "EVAL_POLICY_OPEN",
  "tasks": [
    {
      "task_id": "t01",
      "preview": "Short preview of task",
      "hint": ""
    }
  ]
}
```

---

#### `POST /harness/playground/start`

**Purpose:** Start a playground trial for a single task. Registers the returned `harness_url` in the local `active_trials` registry.

**Request body:**
```json
{
  "benchmark_id": "bitgn/pac1-dev",
  "task_id": "t01"
}
```

**Response 200:**
```json
{
  "trial_id": "trial-abc123",
  "benchmark_id": "bitgn/pac1-dev",
  "task_id": "t01",
  "instruction": "Full task instruction text",
  "harness_url": "https://vm.bitgn.com/trial-abc123"
}
```

**Side effect:** `active_trials["trial-abc123"] = "https://vm.bitgn.com/trial-abc123"`

---

#### `POST /harness/trial/{trial_id}/end`

**Purpose:** End a trial and retrieve the score. Removes trial from local registry.

**Response 200:**
```json
{
  "trial_id": "trial-abc123",
  "state": "TRIAL_STATE_DONE",
  "score": 0.85,
  "score_detail": ["criterion 1: passed", "criterion 2: partial"]
}
```

---

#### `GET /harness/trial/{trial_id}`

**Purpose:** Get trial state and execution logs.

**Query params:**
- `cursor` (int, optional, default 0) — pagination cursor for logs

**Response 200:**
```json
{
  "trial_id": "trial-abc123",
  "instruction": "Full instruction",
  "state": "TRIAL_STATE_RUNNING",
  "score": null,
  "next_cursor": 42,
  "logs": [
    {
      "time": "2026-03-20T10:00:00Z",
      "text": "Agent started",
      "kind": "LOG_KIND_SYSTEM"
    }
  ]
}
```

---

#### `GET /harness/trials/active`

**Purpose:** List all trials currently registered in the local in-memory registry.

**Response 200:**
```json
{
  "active_trials": [
    {
      "trial_id": "trial-abc123",
      "harness_url": "https://vm.bitgn.com/trial-abc123"
    }
  ]
}
```

---

### VM Group — `/vm/{trial_id}`

All VM endpoints require an active `trial_id` registered in `active_trials`. Returns 404 if trial is not found locally.

The dev (PCM) runtime exposes richer filesystem operations than the sandbox Mini runtime: `tree`, `find`, `mkdir`, `move` are new additions.

---

#### `GET /vm/{trial_id}/context`

**Purpose:** Get runtime context for the trial — task instruction, metadata, and other PCM-provided fields.

**No query params.**

**Response 200:**
```json
{
  "instruction": "Full task instruction text",
  "task_id": "t01",
  "benchmark_id": "bitgn/pac1-dev"
}
```

---

#### `GET /vm/{trial_id}/tree`

**Purpose:** Get recursive filesystem tree from a root path.

**Query params:**
- `root` (string, optional, default `""`) — tree root; empty string means repository root

**Response 200:**
```json
{
  "entries": [
    { "path": "/AGENTS.md", "kind": "file" },
    { "path": "/data", "kind": "dir" },
    { "path": "/data/orders.txt", "kind": "file" }
  ]
}
```

---

#### `GET /vm/{trial_id}/find`

**Purpose:** Find files or directories by name pattern.

**Query params:**
- `name` (string, required) — name pattern to search for
- `root` (string, optional, default `/`) — search root
- `kind` (string, optional, default `all`) — one of `all`, `files`, `dirs`
- `limit` (int, optional, default `10`, max `20`) — max results

**Response 200:**
```json
{
  "paths": [
    "/data/invoice_2024.txt",
    "/data/invoice_2025.txt"
  ]
}
```

---

#### `GET /vm/{trial_id}/list`

**Purpose:** List direct children of a directory (no recursion).

**Query params:**
- `path` (string, optional, default `/`)

**Response 200:**
```json
{
  "entries": ["AGENTS.md", "data", "workspace"]
}
```

---

#### `GET /vm/{trial_id}/read`

**Purpose:** Read file contents.

**Query params:**
- `path` (string, required)

**Response 200:**
```json
{
  "path": "/AGENTS.md",
  "content": "# Instructions\n\nRespond with a TODO list..."
}
```

---

#### `POST /vm/{trial_id}/search`

**Purpose:** Full-text search across files.

**Request body:**
```json
{
  "pattern": "invoice",
  "root": "/",
  "limit": 10
}
```

**Response 200:**
```json
{
  "results": [
    {
      "path": "/data/orders.txt",
      "snippet": "invoice #1042",
      "line": 12
    }
  ]
}
```

---

#### `POST /vm/{trial_id}/write`

**Purpose:** Write (create or overwrite) a file.

**Request body:**
```json
{
  "path": "/workspace/output.txt",
  "content": "Hello world"
}
```

**Response 200:**
```json
{ "ok": true }
```

---

#### `DELETE /vm/{trial_id}/file`

**Purpose:** Delete a file.

**Query params:**
- `path` (string, required)

**Response 200:**
```json
{ "ok": true }
```

---

#### `POST /vm/{trial_id}/mkdir`

**Purpose:** Create a directory.

**Request body:**
```json
{
  "path": "/workspace/new_dir"
}
```

**Response 200:**
```json
{ "ok": true }
```

---

#### `POST /vm/{trial_id}/move`

**Purpose:** Move or rename a file or directory.

**Request body:**
```json
{
  "from_name": "/workspace/old.txt",
  "to_name": "/workspace/new.txt"
}
```

**Response 200:**
```json
{ "ok": true }
```

---

#### `POST /vm/{trial_id}/answer`

**Purpose:** Submit the final answer for the trial using a PCM outcome. Call `/harness/trial/{trial_id}/end` after to get the score.

**Request body:**
```json
{
  "message": "Task completed. The total invoice amount is $1,500.",
  "outcome": "OUTCOME_OK",
  "refs": ["/data/invoice.txt", "/AGENTS.md"]
}
```

**`outcome` values:**

| Value | Meaning |
|-------|---------|
| `OUTCOME_OK` | Task completed successfully |
| `OUTCOME_DENIED_SECURITY` | Refused due to security policy |
| `OUTCOME_NONE_CLARIFICATION` | Cannot proceed — needs clarification |
| `OUTCOME_NONE_UNSUPPORTED` | Task not supported |
| `OUTCOME_ERR_INTERNAL` | Internal error during execution |

**Response 200:**
```json
{ "ok": true }
```

**Response 400 (unknown outcome):**
```json
{
  "error": "Unknown outcome 'OUTCOME_XYZ'. Valid values: [...]",
  "code": "BAD_REQUEST"
}
```

---

### Client-side tools (no proxy endpoint)

Two agent tools are implemented client-side and have no corresponding proxy endpoint — they call `/read` internally:

| Tool | Behaviour |
|------|-----------|
| `exists(path)` | Calls `/read?path=`, returns `"EXISTS"` or `"NOT FOUND"` |
| `peek(path, lines)` | Calls `/read?path=`, returns the first N lines of content |

To test these manually, use `GET /vm/{trial_id}/read` directly.

---

## Swagger UI

Served at: `GET /docs`
Spec served at: `GET /apispec.json`

---

## Error Responses

```json
{
  "error": "Human-readable error message",
  "code": "CONNECT_ERROR_CODE or HTTP status"
}
```

Common cases:
- `trial_id` not in `active_trials` → 404
- ConnectError from gRPC → 502
- Missing required body fields → 400
- Unknown `outcome` value → 400

---

## VM Operation Comparison: Sandbox vs Dev

| Operation | Sandbox (Mini) | Dev (PCM) |
|-----------|---------------|-----------|
| Context / runtime info | — | `GET /context` |
| Tree / outline | `GET /outline?path=` | `GET /tree?root=` |
| Find by name | — | `GET /find?name=&kind=` |
| List directory | `GET /list?path=` | `GET /list?path=` |
| Read file | `GET /read?path=` | `GET /read?path=` |
| Search text | `POST /search` (`path`, `count`) | `POST /search` (`root`, `limit`) |
| Write file | `POST /write` | `POST /write` |
| Delete file | `DELETE /file?path=` | `DELETE /file?path=` |
| Make directory | — | `POST /mkdir` |
| Move / rename | — | `POST /move` |
| Submit answer | `POST /answer` (`answer`, `refs`) | `POST /answer` (`message`, `outcome`, `refs`) |

---

## File layout

```
dev/py/
├── app_dev.py               # Flask proxy (dev/pac-dev) — this server
├── API_SPEC_dev.md          # this file
├── agent.py                 # simple dev agent (direct PCM, OpenAI structured output)
├── main.py                  # simple benchmark runner
├── main_pipeline.py         # pipeline-based benchmark runner
├── agent_pipeline_openai/   # OpenAI Agents SDK pipeline
│   ├── react.py             # ReAct loop + tool definitions (13 tools)
│   ├── pipeline.py          # stage orchestration
│   ├── context.py           # ContextBuilderStage (rule graph)
│   ├── planning.py          # PlanningStage
│   ├── verifier.py          # VerifierStage
│   └── capability_check.py  # CapabilityCheckStage
├── agent_pipeline_claude/   # Claude pipeline
├── run_openai_dev.sh        # runner script (OpenAI pipeline)
├── run_claude_dev.sh        # runner script (Claude pipeline)
├── trace_viewer.html        # local trace viewer
└── pyproject.toml           # bitgn-pac1-py project
```
