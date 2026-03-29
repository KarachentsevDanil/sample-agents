#!/usr/bin/env python3
"""
pull_agent_file_system.py <task_id>

Starts a fresh trial for the given task, DFS-traverses the VM filesystem via
the contest API, and mirrors every directory and file to:

    runs/codex_api/<task_id>/fileSystem/

Trial is left active after the pull (not ended).
Trial metadata is saved alongside at:

    runs/codex_api/<task_id>/trial_meta.json

Usage:
    python pull_agent_file_system.py t01
    python pull_agent_file_system.py t11
"""

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE_URL = "http://localhost:8081"
BENCHMARK_ID = "bitgn/pac1-dev"


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _get(path: str) -> dict:
    url = f"{BASE_URL}{path}"
    with urllib.request.urlopen(url) as resp:
        return json.loads(resp.read())


def _post(path: str, body: dict) -> dict:
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


# ---------------------------------------------------------------------------
# API calls
# ---------------------------------------------------------------------------

def start_trial(task_id: str) -> tuple[str, str]:
    resp = _post("/harness/playground/start", {
        "benchmark_id": BENCHMARK_ID,
        "task_id": task_id,
    })
    return resp["trial_id"], resp["instruction"]


def get_tree(trial_id: str) -> dict:
    return _get(f"/vm/{trial_id}/tree?root=")


def read_file(trial_id: str, api_path: str) -> str:
    encoded = urllib.parse.quote(api_path, safe="/")
    resp = _get(f"/vm/{trial_id}/read?path={encoded}")
    return resp.get("content", "")


# ---------------------------------------------------------------------------
# DFS mirror
# ---------------------------------------------------------------------------

def _dfs(trial_id: str, node: dict, node_api_path: str, out_root: Path, stats: dict) -> None:
    """
    Recursively mirror one tree node.

    node_api_path: leading-slash API path for this node, e.g. '/outbox/seq.json'
    The root node itself is called with node_api_path='/'.
    """
    is_dir = node.get("is_dir", False)

    if is_dir:
        if node_api_path != "/":
            local_dir = out_root / node_api_path.lstrip("/")
            local_dir.mkdir(parents=True, exist_ok=True)
            stats["dirs"] += 1
            print(f"  [DIR]  {node_api_path}/")

        for child in node.get("children", []):
            child_name = child["name"]
            # '/'.rstrip('/') == '' so root children get '/child_name' correctly
            child_api_path = f"{node_api_path.rstrip('/')}/{child_name}"
            _dfs(trial_id, child, child_api_path, out_root, stats)

    else:
        local_file = out_root / node_api_path.lstrip("/")
        local_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            content = read_file(trial_id, node_api_path)
            local_file.write_text(content, encoding="utf-8")
            stats["files"] += 1
            print(f"  [FILE] {node_api_path}  ({len(content)} chars)")
        except urllib.error.HTTPError as exc:
            print(f"  [SKIP] {node_api_path}  (read failed: HTTP {exc.code})")
            stats["skipped"] += 1


def mirror_filesystem(trial_id: str, out_root: Path) -> dict:
    print("Fetching filesystem tree ...")
    tree_resp = get_tree(trial_id)
    root_node = tree_resp["root"]

    out_root.mkdir(parents=True, exist_ok=True)
    stats: dict = {"dirs": 0, "files": 0, "skipped": 0}

    print("Mirroring ...")
    _dfs(trial_id, root_node, "/", out_root, stats)
    return stats


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: pull_agent_file_system.py <task_id>")
        print("  e.g.: python pull_agent_file_system.py t01")
        sys.exit(1)

    task_id = sys.argv[1]
    script_dir = Path(__file__).parent
    run_dir = script_dir / "runs" / "codex_api" / task_id
    out_root = run_dir / "fileSystem"

    print(f"task_id : {task_id}")
    print(f"output  : {out_root}")
    print()

    print("Starting trial ...")
    trial_id, instruction = start_trial(task_id)
    print(f"trial_id    : {trial_id}")
    print(f"instruction : {instruction}")
    print()

    # Persist trial metadata so callers can reuse trial_id
    run_dir.mkdir(parents=True, exist_ok=True)
    meta = {
        "task_id": task_id,
        "trial_id": trial_id,
        "instruction": instruction,
        "benchmark_id": BENCHMARK_ID,
    }
    meta_path = run_dir / "trial_meta.json"
    meta_path.write_text(json.dumps(meta, indent=2))

    stats = mirror_filesystem(trial_id, out_root)

    print()
    print(f"Done: {stats['dirs']} dirs, {stats['files']} files"
          + (f", {stats['skipped']} skipped" if stats["skipped"] else ""))
    print(f"Trial {trial_id} is still active (not ended).")
    print(f"Metadata : {meta_path}")
    print(f"FileSystem: {out_root}")


if __name__ == "__main__":
    main()
