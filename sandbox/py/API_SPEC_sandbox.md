# BitGN Debug Proxy — Sandbox API Specification

## Overview

A Flask server that proxies the BitGN harness and Mini VM runtime gRPC services over REST+JSON and exposes a Swagger UI for interactive debugging. Maintains an in-memory registry of active trials so VM calls only need a `trial_id` — the `harness_url` is looked up automatically.

Benchmark: `bitgn/sandbox`

---

## Architecture

```
Browser / Swagger UI
        │
        ▼
  Flask app_sandbox.py  (port 8080)
        │
   ┌────┴────────────────────────────────────┐
   │                                         │
   ▼                                         ▼
HarnessServiceClientSync               MiniRuntimeClientSync
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
bitgn                    # existing SDK (already installed)
```

---

## Configuration

| Env var | Default | Description |
|---------|---------|-------------|
| `BENCHMARK_HOST` | `https://api.bitgn.com` | BitGN harness URL |
| `PORT` | `8080` | Flask listen port |

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
- `benchmark_id` (string, required) — e.g. `bitgn/sandbox`

**Response 200:**
```json
{
  "benchmark_id": "bitgn/sandbox",
  "description": "Sandbox benchmark",
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
  "benchmark_id": "bitgn/sandbox",
  "task_id": "t01"
}
```

**Response 200:**
```json
{
  "trial_id": "trial-abc123",
  "benchmark_id": "bitgn/sandbox",
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

---

#### `GET /vm/{trial_id}/outline`

**Purpose:** Get recursive filesystem tree from a path.

**Query params:**
- `path` (string, optional, default `/`)

**Response 200:**
```json
{
  "path": "/",
  "folders": ["/data", "/workspace"],
  "files": [
    {
      "path": "/AGENTS.md",
      "headers": ["# Instructions"]
    }
  ]
}
```

---

#### `GET /vm/{trial_id}/list`

**Purpose:** List direct children of a directory (no recursion).

**Query params:**
- `path` (string, required)

**Response 200:**
```json
{
  "folders": ["data", "workspace"],
  "files": ["AGENTS.md", "README.txt"]
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
  "path": "/",
  "count": 5
}
```

**Response 200:**
```json
{
  "snippets": [
    {
      "file": "/data/orders.txt",
      "match": "invoice #1042",
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

#### `POST /vm/{trial_id}/answer`

**Purpose:** Submit the final answer for the trial. Call `/harness/trial/{trial_id}/end` after to get the score.

**Request body:**
```json
{
  "answer": "The total invoice amount is $1,500",
  "refs": ["/data/invoice.txt", "/AGENTS.md"]
}
```

**Response 200:**
```json
{ "ok": true }
```

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

---

## File layout

```
sandbox/py/
├── agent.py             # existing sandbox agent
├── main.py              # existing sandbox runner
├── app.py               # Flask proxy (sandbox, canonical)
├── app_sandbox.py       # Flask proxy (sandbox, explicit copy)
└── API_SPEC_sandbox.md  # this file
```
