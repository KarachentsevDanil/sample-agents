# BitGN Debug Proxy — API Specification

## Overview

A Flask server that proxies the BitGN harness and VM gRPC services over REST+JSON and exposes a Swagger UI for interactive debugging. Maintains an in-memory registry of active trials so VM calls only need a `trial_id` — the `harness_url` is looked up automatically.

---

## Architecture

```
Browser / Swagger UI
        │
        ▼
  Flask app.py  (port 5000)
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
flask-swagger-ui         # serves /docs Swagger UI
flasgger                 # generates OpenAPI spec from docstrings
bitgn                    # existing SDK (already installed)
```

---

## Configuration

| Env var | Default | Description |
|---------|---------|-------------|
| `BENCHMARK_HOST` | `https://api.bitgn.com` | BitGN harness URL |
| `DEFAULT_BENCHMARK_ID` | `bitgn/sandbox` | Used as default in requests |
| `PORT` | `5000` | Flask listen port |

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
  "links": [
    { "url": "https://...", "kind": "LINK_KIND_LANDING" }
  ],
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

**Path params:**
- `trial_id` (string, required)

**Response 200:**
```json
{
  "trial_id": "trial-abc123",
  "state": "TRIAL_STATE_DONE",
  "score": 0.85,
  "score_detail": [
    "criterion 1: passed",
    "criterion 2: partial"
  ]
}
```

**Side effect:** removes `trial_id` from `active_trials`

---

#### `GET /harness/trial/{trial_id}`

**Purpose:** Get trial state and execution logs (useful for live monitoring).

**Path params:**
- `trial_id` (string, required)

**Query params:**
- `cursor` (int, optional, default 0) — pagination cursor for logs

**Response 200:**
```json
{
  "trial_id": "trial-abc123",
  "instruction": "Full instruction",
  "benchmark_id": "bitgn/sandbox",
  "task_id": "t01",
  "state": "TRIAL_STATE_RUNNING",
  "score": null,
  "score_detail": [],
  "error": "",
  "next_cursor": 42,
  "logs": [
    {
      "time": "2026-03-20T10:00:00Z",
      "unix_ms": 1742468400000,
      "text": "Agent started",
      "kind": "LOG_KIND_SYSTEM",
      "type": "info",
      "data": null
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
- `path` (string, optional, default `/`) — root path to outline

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
- `path` (string, required) — directory to list

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
- `path` (string, required) — file path

**Response 200:**
```json
{
  "path": "/AGENTS.md",
  "content": "# Instructions\n\nRespond with a TODO list..."
}
```

**Response 404:**
```json
{ "error": "File not found: /AGENTS.md" }
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
- `path` (string, required) — path to delete

**Response 200:**
```json
{ "ok": true }
```

---

#### `POST /vm/{trial_id}/answer`

**Purpose:** Submit the final answer for the trial (equivalent to `report_completion`). This does NOT end the trial — call `/harness/trial/{trial_id}/end` after.

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

The Swagger UI should group endpoints by tag:
- `harness` — harness management (benchmark, trials)
- `vm` — VM file operations and answer submission

---

## Error Responses

All endpoints return consistent error shape on failure:

**Response 4xx / 5xx:**
```json
{
  "error": "Human-readable error message",
  "code": "CONNECT_ERROR_CODE or HTTP status"
}
```

Common cases:
- `trial_id` not in `active_trials` → 404 `Trial not registered. Call /harness/playground/start first.`
- ConnectError from gRPC → 502 with gRPC error message
- Missing required body fields → 400 with field name

---

## Implementation Notes

1. **Single file** — implement everything in `app.py`, ~200-250 lines. No blueprints needed.
2. **flasgger** for Swagger — use YAML docstrings in each route function; `Swagger(app)` auto-generates the spec.
3. **active_trials dict** — module-level `dict`, no persistence needed (debug tool only).
4. **MessageToDict** — use `google.protobuf.json_format.MessageToDict` for all protobuf → JSON conversions (already used in agent.py).
5. **ConnectError handling** — wrap all gRPC calls in try/except, return 502 with `e.message`.
6. **CORS** — add `flask-cors` to allow browser requests from any origin.
7. **Run command** — `uv run python app.py` or `flask --app app run`.

---

## File layout

```
sandbox/py/
├── agent.py        # existing
├── main.py         # existing
├── app.py          # NEW — Flask proxy server
└── API_SPEC.md     # this file
```

No new packages needed beyond `flask`, `flasgger`, `flask-cors` — add to `pyproject.toml`.
