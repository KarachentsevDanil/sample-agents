"""
BitGN Debug Proxy — Sandbox variant (Flask server with Swagger UI).

Proxies the BitGN harness (gRPC/ConnectRPC) over plain REST+JSON and exposes
all VM file-system operations per trial using the Mini runtime (bitgn/sandbox).
Run with:

    uv run python app_sandbox.py

Swagger UI:  http://localhost:8080/docs
"""
import os

from flasgger import Swagger
from flask import Flask, jsonify, request
from flask_cors import CORS
from google.protobuf.json_format import MessageToDict

from bitgn.harness_connect import HarnessServiceClientSync
from bitgn.harness_pb2 import (
    EndTrialRequest,
    GetBenchmarkRequest,
    GetTrialRequest,
    StartPlaygroundRequest,
    StatusRequest,
)
from bitgn.vm.mini_connect import MiniRuntimeClientSync
from bitgn.vm.mini_pb2 import (
    AnswerRequest,
    DeleteRequest,
    ListRequest,
    OutlineRequest,
    ReadRequest,
    SearchRequest,
    WriteRequest,
)
from connectrpc.errors import ConnectError

BITGN_URL = os.getenv("BENCHMARK_HOST") or "https://api.bitgn.com"
PORT = int(os.getenv("PORT", "8080"))

app = Flask(__name__)
CORS(app)

swagger_config = {
    "headers": [],
    "specs": [
        {
            "endpoint": "apispec",
            "route": "/apispec.json",
            "rule_filter": lambda rule: True,
            "model_filter": lambda tag: True,
        }
    ],
    "static_url_path": "/flasgger_static",
    "swagger_ui": True,
    "specs_route": "/docs",
}

swagger_template = {
    "info": {
        "title": "BitGN Debug Proxy — Sandbox",
        "description": (
            "Interactive REST proxy for the BitGN harness and Mini VM runtime (bitgn/sandbox). "
            "Start a playground trial, then use the VM endpoints to read/write files and submit answers."
        ),
        "version": "1.0.0",
    },
    "tags": [
        {"name": "harness", "description": "Harness management — benchmarks and trials"},
        {"name": "vm", "description": "VM file-system operations (requires active trial_id)"},
    ],
}

Swagger(app, config=swagger_config, template=swagger_template)

# In-memory registry: trial_id → harness_url
active_trials: dict[str, str] = {}

harness = HarnessServiceClientSync(BITGN_URL)


def _vm(trial_id: str):
    """Return a MiniRuntimeClientSync for the given trial, or raise KeyError."""
    url = active_trials.get(trial_id)
    if not url:
        raise KeyError(trial_id)
    return MiniRuntimeClientSync(url)


def _proto(msg) -> dict:
    return MessageToDict(msg, preserving_proto_field_name=True)


def _connect_err(e: ConnectError):
    return jsonify({"error": e.message, "code": str(e.code)}), 502


def _not_found(trial_id: str):
    return jsonify({
        "error": f"Trial '{trial_id}' not registered. Call POST /harness/playground/start first.",
        "code": "NOT_FOUND",
    }), 404


# ---------------------------------------------------------------------------
# Harness endpoints
# ---------------------------------------------------------------------------

@app.get("/harness/status")
def harness_status():
    """
    Check harness connectivity.
    ---
    tags: [harness]
    responses:
      200:
        description: Harness status
        schema:
          type: object
          properties:
            status: {type: string}
            version: {type: string}
      502:
        description: ConnectRPC error
    """
    try:
        resp = harness.status(StatusRequest())
        return jsonify(_proto(resp))
    except ConnectError as e:
        return _connect_err(e)


@app.get("/harness/benchmark/<path:benchmark_id>")
def get_benchmark(benchmark_id):
    """
    Get benchmark metadata and task list.
    ---
    tags: [harness]
    parameters:
      - name: benchmark_id
        in: path
        required: true
        type: string
        default: bitgn/sandbox
        description: Benchmark ID (e.g. bitgn/sandbox)
    responses:
      200:
        description: Benchmark details with task list
      502:
        description: ConnectRPC error
    """
    try:
        resp = harness.get_benchmark(GetBenchmarkRequest(benchmark_id=benchmark_id))
        return jsonify(_proto(resp))
    except ConnectError as e:
        return _connect_err(e)


@app.post("/harness/playground/start")
def start_playground():
    """
    Start a playground trial for a single task.
    Registers the harness_url locally so VM endpoints work automatically.
    ---
    tags: [harness]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [benchmark_id, task_id]
          properties:
            benchmark_id:
              type: string
              default: bitgn/sandbox
            task_id:
              type: string
              default: t01
    responses:
      200:
        description: Trial started — use trial_id in VM endpoints
      400:
        description: Missing required fields
      502:
        description: ConnectRPC error
    """
    body = request.get_json(force=True) or {}
    benchmark_id = body.get("benchmark_id")
    task_id = body.get("task_id")
    if not benchmark_id or not task_id:
        return jsonify({"error": "benchmark_id and task_id are required", "code": "BAD_REQUEST"}), 400

    try:
        resp = harness.start_playground(StartPlaygroundRequest(
            benchmark_id=benchmark_id, task_id=task_id,
        ))
        active_trials[resp.trial_id] = resp.harness_url
        return jsonify(_proto(resp))
    except ConnectError as e:
        return _connect_err(e)


@app.post("/harness/trial/<trial_id>/end")
def end_trial(trial_id):
    """
    End a trial and retrieve the score.
    Removes the trial from the local registry.
    ---
    tags: [harness]
    parameters:
      - name: trial_id
        in: path
        required: true
        type: string
    responses:
      200:
        description: Trial result with score and score_detail
      502:
        description: ConnectRPC error
    """
    try:
        resp = harness.end_trial(EndTrialRequest(trial_id=trial_id))
        active_trials.pop(trial_id, None)
        return jsonify(_proto(resp))
    except ConnectError as e:
        return _connect_err(e)


@app.get("/harness/trial/<trial_id>")
def get_trial(trial_id):
    """
    Get trial state and execution logs.
    ---
    tags: [harness]
    parameters:
      - name: trial_id
        in: path
        required: true
        type: string
      - name: cursor
        in: query
        type: integer
        default: 0
        description: Pagination cursor for logs
    responses:
      200:
        description: Trial state and logs
      502:
        description: ConnectRPC error
    """
    cursor = int(request.args.get("cursor", 0))
    try:
        resp = harness.get_trial(GetTrialRequest(trial_id=trial_id, cursor=cursor))
        return jsonify(_proto(resp))
    except ConnectError as e:
        return _connect_err(e)


@app.get("/harness/trials/active")
def active_trials_list():
    """
    List all trials currently registered in the local registry.
    ---
    tags: [harness]
    responses:
      200:
        description: Active trial registry
        schema:
          type: object
          properties:
            active_trials:
              type: array
              items:
                type: object
                properties:
                  trial_id: {type: string}
                  harness_url: {type: string}
    """
    return jsonify({
        "active_trials": [
            {"trial_id": tid, "harness_url": url}
            for tid, url in active_trials.items()
        ]
    })


# ---------------------------------------------------------------------------
# VM endpoints
# ---------------------------------------------------------------------------

@app.get("/vm/<trial_id>/outline")
def vm_outline(trial_id):
    """
    Get recursive filesystem tree.
    ---
    tags: [vm]
    parameters:
      - name: trial_id
        in: path
        required: true
        type: string
      - name: path
        in: query
        type: string
        default: /
    responses:
      200:
        description: Directory tree
      404:
        description: Trial not registered
      502:
        description: ConnectRPC error
    """
    try:
        vm = _vm(trial_id)
    except KeyError:
        return _not_found(trial_id)

    path = request.args.get("path", "/")
    try:
        resp = vm.outline(OutlineRequest(path=path))
        return jsonify(_proto(resp))
    except ConnectError as e:
        return _connect_err(e)


@app.get("/vm/<trial_id>/list")
def vm_list(trial_id):
    """
    List direct children of a directory.
    ---
    tags: [vm]
    parameters:
      - name: trial_id
        in: path
        required: true
        type: string
      - name: path
        in: query
        required: true
        type: string
        default: /
    responses:
      200:
        description: Directory listing
      404:
        description: Trial not registered
      502:
        description: ConnectRPC error
    """
    try:
        vm = _vm(trial_id)
    except KeyError:
        return _not_found(trial_id)

    path = request.args.get("path", "/")
    try:
        resp = vm.list(ListRequest(path=path))
        return jsonify(_proto(resp))
    except ConnectError as e:
        return _connect_err(e)


@app.get("/vm/<trial_id>/read")
def vm_read(trial_id):
    """
    Read file contents.
    ---
    tags: [vm]
    parameters:
      - name: trial_id
        in: path
        required: true
        type: string
      - name: path
        in: query
        required: true
        type: string
        default: /AGENTS.md
    responses:
      200:
        description: File path and content
      400:
        description: Missing path parameter
      404:
        description: Trial not registered
      502:
        description: ConnectRPC error
    """
    try:
        vm = _vm(trial_id)
    except KeyError:
        return _not_found(trial_id)

    path = request.args.get("path")
    if not path:
        return jsonify({"error": "path query parameter is required", "code": "BAD_REQUEST"}), 400

    try:
        resp = vm.read(ReadRequest(path=path))
        return jsonify(_proto(resp))
    except ConnectError as e:
        return _connect_err(e)


@app.post("/vm/<trial_id>/search")
def vm_search(trial_id):
    """
    Full-text search across VM files.
    ---
    tags: [vm]
    parameters:
      - name: trial_id
        in: path
        required: true
        type: string
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [pattern]
          properties:
            pattern:
              type: string
              default: invoice
            path:
              type: string
              default: /
            count:
              type: integer
              default: 5
              minimum: 1
              maximum: 10
    responses:
      200:
        description: Matching snippets
      400:
        description: Missing pattern
      404:
        description: Trial not registered
      502:
        description: ConnectRPC error
    """
    try:
        vm = _vm(trial_id)
    except KeyError:
        return _not_found(trial_id)

    body = request.get_json(force=True) or {}
    pattern = body.get("pattern")
    if not pattern:
        return jsonify({"error": "pattern is required", "code": "BAD_REQUEST"}), 400

    path = body.get("path", "/")
    count = int(body.get("count", 5))

    try:
        resp = vm.search(SearchRequest(path=path, pattern=pattern, count=count))
        return jsonify(_proto(resp))
    except ConnectError as e:
        return _connect_err(e)


@app.post("/vm/<trial_id>/write")
def vm_write(trial_id):
    """
    Write (create or overwrite) a file.
    ---
    tags: [vm]
    parameters:
      - name: trial_id
        in: path
        required: true
        type: string
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [path, content]
          properties:
            path:
              type: string
              default: /workspace/output.txt
            content:
              type: string
              default: Hello world
    responses:
      200:
        description: File written
      400:
        description: Missing path or content
      404:
        description: Trial not registered
      502:
        description: ConnectRPC error
    """
    try:
        vm = _vm(trial_id)
    except KeyError:
        return _not_found(trial_id)

    body = request.get_json(force=True) or {}
    path = body.get("path")
    content = body.get("content")
    if not path or content is None:
        return jsonify({"error": "path and content are required", "code": "BAD_REQUEST"}), 400

    try:
        vm.write(WriteRequest(path=path, content=content))
        return jsonify({"ok": True})
    except ConnectError as e:
        return _connect_err(e)


@app.delete("/vm/<trial_id>/file")
def vm_delete(trial_id):
    """
    Delete a file.
    ---
    tags: [vm]
    parameters:
      - name: trial_id
        in: path
        required: true
        type: string
      - name: path
        in: query
        required: true
        type: string
    responses:
      200:
        description: File deleted
      400:
        description: Missing path parameter
      404:
        description: Trial not registered
      502:
        description: ConnectRPC error
    """
    try:
        vm = _vm(trial_id)
    except KeyError:
        return _not_found(trial_id)

    path = request.args.get("path")
    if not path:
        return jsonify({"error": "path query parameter is required", "code": "BAD_REQUEST"}), 400

    try:
        vm.delete(DeleteRequest(path=path))
        return jsonify({"ok": True})
    except ConnectError as e:
        return _connect_err(e)


@app.post("/vm/<trial_id>/answer")
def vm_answer(trial_id):
    """
    Submit the final answer for the trial.
    Call POST /harness/trial/{trial_id}/end afterward to get the score.
    ---
    tags: [vm]
    parameters:
      - name: trial_id
        in: path
        required: true
        type: string
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [answer]
          properties:
            answer:
              type: string
              default: The total is $1500
            refs:
              type: array
              items: {type: string}
              default: []
    responses:
      200:
        description: Answer submitted
      400:
        description: Missing answer
      404:
        description: Trial not registered
      502:
        description: ConnectRPC error
    """
    try:
        vm = _vm(trial_id)
    except KeyError:
        return _not_found(trial_id)

    body = request.get_json(force=True) or {}
    answer = body.get("answer")
    if not answer:
        return jsonify({"error": "answer is required", "code": "BAD_REQUEST"}), 400

    refs = body.get("refs", [])
    try:
        vm.answer(AnswerRequest(answer=answer, refs=refs))
        return jsonify({"ok": True})
    except ConnectError as e:
        return _connect_err(e)


if __name__ == "__main__":
    print(f"Starting BitGN Debug Proxy (Sandbox) on http://localhost:{PORT}")
    print(f"Swagger UI: http://localhost:{PORT}/docs")
    print(f"Harness:    {BITGN_URL}")
    app.run(host="0.0.0.0", port=PORT, debug=True)
