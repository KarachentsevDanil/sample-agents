"""ReAct loop tools — 7 VM-backed tools for the ReAct agent."""

import json
import time
from pathlib import PurePosixPath
from typing import Any, List

from connectrpc.errors import ConnectError
from google.protobuf.json_format import MessageToDict

from bitgn.vm.pcm_pb2 import (
    AnswerRequest,
    DeleteRequest,
    FindRequest,
    ListRequest,
    MkDirRequest,
    MoveRequest,
    Outcome,
    ReadRequest,
    SearchRequest,
    TreeRequest,
    WriteRequest,
)

from ..infra._cli import CLI_RED, CLI_GREEN, CLI_CLR
from ..models import AgentRuntimeContext, PipelineContext
from ..infra.validator import validate_or_error

OUTCOME_BY_NAME = {
    "OUTCOME_OK": Outcome.OUTCOME_OK,
    "OUTCOME_DENIED_SECURITY": Outcome.OUTCOME_DENIED_SECURITY,
    "OUTCOME_NONE_CLARIFICATION": Outcome.OUTCOME_NONE_CLARIFICATION,
    "OUTCOME_NONE_UNSUPPORTED": Outcome.OUTCOME_NONE_UNSUPPORTED,
    "OUTCOME_ERR_INTERNAL": Outcome.OUTCOME_ERR_INTERNAL,
}

_STEP_BUDGET_WARNING = (
    "\n\n** STEP BUDGET WARNING: You have used {used}/{max} steps. "
    "You MUST call done() on your NEXT action. "
    "Summarize what you've done and choose the correct OUTCOME code."
)


# ── Helpers ──────────────────────────────────────────────────────

def _clip(text: str, limit: int = 80) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _json_or_none(text: str):
    try:
        return json.loads(text)
    except Exception:
        return None


def _count_lines(text: str) -> int:
    if not text:
        return 0
    return len(text.splitlines()) or 1


def _tool_target(command: str, args: dict) -> str:
    if command in {"read_file", "write_file", "delete_file"}:
        return args.get("path", "")
    if command == "browse":
        mode = args.get("mode", "tree")
        path = args.get("path", "/")
        if mode == "find":
            return f"find {args.get('name', '')} @ {path}"
        return f"{mode} {path}"
    if command == "search":
        pattern = args.get("pattern", "")
        root = args.get("root", "/")
        return f"{pattern} @ {root}"
    if command == "move_file":
        return f"{args.get('from_path', '')} -> {args.get('to_path', '')}"
    return ""


def _tool_summary(command: str, args: dict, result_text: str) -> str:
    if command == "read_file":
        if result_text == "NOT_FOUND":
            return "NOT_FOUND"
        return f"{_count_lines(result_text)} lines"
    if command == "done":
        return args.get("outcome", "OUTCOME_OK")

    payload = _json_or_none(result_text)
    if isinstance(payload, dict):
        if command == "browse":
            mode = args.get("mode", "tree")
            if mode == "tree":
                root = payload.get("root") or {}
                children = root.get("children") or []
                return f"{len(children)} children"
            if mode == "ls":
                entries = payload.get("entries") or []
                return f"{len(entries)} entries"
            if mode == "find":
                items = payload.get("items") or payload.get("paths") or []
                return f"{len(items)} items"
        if command == "search":
            matches = payload.get("matches") or payload.get("results") or []
            return f"{len(matches)} matches"
        if command in {"write_file", "delete_file", "move_file"}:
            ok = payload.get("ok")
            if ok is True or payload == {}:
                return "ok"

    if result_text.startswith("OK. Verified"):
        return result_text
    if result_text.startswith("DELETED"):
        return "DELETED"
    if result_text.startswith("ERROR:"):
        return _clip(result_text, 120)
    if result_text == "{}":
        return "ok"
    return _clip(result_text.replace("\n", " "), 120)


def _print_tool_step(step_idx: int, command: str, args: dict, result_text: str, ok: bool = True) -> None:
    target = _clip(_tool_target(command, args), 90)
    summary = _clip(_tool_summary(command, args, result_text), 120)
    target_part = f" {target}" if target else ""
    color = CLI_GREEN if ok else CLI_RED
    print(f"Step {step_idx}: {command}{target_part} {color}-> {summary}{CLI_CLR}")


def _tool_result(ctx: AgentRuntimeContext, command: str, args: dict, result_text: str) -> str:
    ctx.step_idx += 1
    now = time.time()
    step_duration_ms = int((now - ctx.last_step_ts) * 1000) if ctx.last_step_ts > 0 else None
    ctx.last_step_ts = now
    ctx.logger.append_api_call({
        "step": ctx.step_idx,
        "cmd": command,
        "args": args,
        "result": result_text,
        "ts": now,
    })
    step_record = {
        "step": ctx.step_idx,
        "node_id": str(ctx.step_idx),
        "cmd": command,
        "args": args,
        "result": result_text,
        "ts": now,
        "step_duration_ms": step_duration_ms,
    }
    ctx.pipeline.react_trace.append(step_record)
    ctx.logger.append_react_step(step_record)
    _print_tool_step(ctx.step_idx, command, args, result_text, ok=True)
    # Plan progress tracking
    if ctx.pipeline.task_plan:
        _update_plan_progress(ctx.pipeline, command, ctx.step_idx)
    # Inject step budget warning when approaching the limit
    max_steps = ctx.pipeline.react_max_steps
    if command != "done" and ctx.step_idx >= max_steps - 3:
        result_text += _STEP_BUDGET_WARNING.format(used=ctx.step_idx, max=max_steps)
    return result_text


def _tool_error(ctx: AgentRuntimeContext, command: str, args: dict, err: ConnectError) -> str:
    ctx.step_idx += 1
    message = err.message or str(err)
    ctx.logger.append_api_call({
        "step": ctx.step_idx,
        "cmd": command,
        "args": args,
        "error": message,
        "code": str(err.code),
        "ts": time.time(),
    })
    step_record = {
        "step": ctx.step_idx,
        "node_id": str(ctx.step_idx),
        "cmd": command,
        "args": args,
        "result": message,
        "ts": time.time(),
    }
    ctx.pipeline.react_trace.append(step_record)
    ctx.logger.append_react_step(step_record)
    _print_tool_step(ctx.step_idx, command, args, message, ok=False)
    return message


def _update_plan_progress(pipeline_ctx: PipelineContext, tool_name: str, step_count: int) -> None:
    """Mark the next incomplete plan step as done if the tool matches expected_tools."""
    for entry in pipeline_ctx.plan_progress:
        if entry["done"]:
            continue
        plan_step = next(
            (s for s in pipeline_ctx.task_plan.steps if s.id == entry["step_id"]), None
        )
        if plan_step and tool_name in plan_step.expected_tools:
            entry["done"] = True
            entry["completed_at_react_step"] = step_count
        break


def _record_file_use(ctx: AgentRuntimeContext, path: str) -> None:
    if path and path not in ctx.pipeline.files_used:
        ctx.pipeline.files_used.append(path)


# ── 7 Tools (plain functions) ────────────────────────────────────


def read_file(ctx: AgentRuntimeContext, path: str) -> str:
    """Read a file. Returns file content, or 'NOT_FOUND' if the file doesn't exist."""
    req_args = {"path": path}
    try:
        resp = ctx.vm.read(ReadRequest(path=path))
        text = resp.content
        _record_file_use(ctx, path)
        return _tool_result(ctx, "read_file", req_args, text)
    except ConnectError:
        return _tool_result(ctx, "read_file", req_args, "NOT_FOUND")


def browse(
    ctx: AgentRuntimeContext,
    path: str = "/",
    mode: str = "tree",
    name: str = "",
) -> str:
    """Browse the filesystem."""
    req_args = {"path": path, "mode": mode, "name": name}
    try:
        if mode == "tree":
            resp = ctx.vm.tree(TreeRequest(root=path))
            text = json.dumps(MessageToDict(resp), indent=2)
        elif mode == "ls":
            resp = ctx.vm.list(ListRequest(name=path))
            text = json.dumps(MessageToDict(resp), indent=2)
        elif mode == "find":
            resp = ctx.vm.find(FindRequest(
                name=name, root=path, type=0, limit=10,
            ))
            text = json.dumps(MessageToDict(resp), indent=2)
        else:
            text = f"ERROR: Unknown mode '{mode}'. Use 'tree', 'ls', or 'find'."
        return _tool_result(ctx, "browse", req_args, text)
    except ConnectError as err:
        return _tool_error(ctx, "browse", req_args, err)


def search(
    ctx: AgentRuntimeContext,
    pattern: str,
    root: str = "/",
    limit: int = 10,
) -> str:
    """Search file contents using a regex or substring pattern."""
    req_args = {"pattern": pattern, "root": root, "limit": limit}
    try:
        resp = ctx.vm.search(SearchRequest(root=root, pattern=pattern, limit=limit))
        text = json.dumps(MessageToDict(resp), indent=2)
        return _tool_result(ctx, "search", req_args, text)
    except ConnectError as err:
        return _tool_error(ctx, "search", req_args, err)


def write_file(ctx: AgentRuntimeContext, path: str, content: str) -> str:
    """Write content to a file. Creates parent directories if needed."""
    req_args = {"path": path, "content": content}
    err = validate_or_error(ctx, "write", req_args)
    if err:
        print(f"{CLI_RED}{err}{CLI_CLR}")
        return err

    # Template protection guard
    filename = path.split("/")[-1].lower()
    if filename.startswith("_") or "template" in filename:
        error_msg = f"ERROR: Cannot write to template/scaffolding file: {path}"
        return _tool_result(ctx, "write_file", req_args, error_msg)

    # Auto-mkdir parent
    parent = str(PurePosixPath(path).parent)
    if parent and parent != "/" and parent != ".":
        try:
            ctx.vm.mk_dir(MkDirRequest(path=parent))
        except ConnectError:
            pass

    try:
        ctx.vm.write(WriteRequest(path=path, content=content.rstrip("\n")))
        # Auto-verify by reading back
        verify = ctx.vm.read(ReadRequest(path=path))
        _record_file_use(ctx, path)
        result = f"OK. Verified content ({len(verify.content)} chars)"
        return _tool_result(ctx, "write_file", req_args, result)
    except ConnectError as err:
        return _tool_error(ctx, "write_file", req_args, err)


def delete_file(ctx: AgentRuntimeContext, path: str) -> str:
    """Delete a file or directory."""
    req_args = {"path": path}
    err = validate_or_error(ctx, "delete", req_args)
    if err:
        print(f"{CLI_RED}{err}{CLI_CLR}")
        return err

    # Template protection guard
    filename = path.split("/")[-1].lower()
    if filename.startswith("_") or "template" in filename:
        error_msg = f"ERROR: Cannot delete template/scaffolding file: {path}"
        return _tool_result(ctx, "delete_file", req_args, error_msg)

    try:
        ctx.vm.delete(DeleteRequest(path=path))
        return _tool_result(ctx, "delete_file", req_args, "DELETED")
    except ConnectError as err:
        return _tool_error(ctx, "delete_file", req_args, err)


def move_file(ctx: AgentRuntimeContext, from_path: str, to_path: str) -> str:
    """Move or rename a file."""
    req_args = {"from_path": from_path, "to_path": to_path}
    try:
        resp = ctx.vm.move(MoveRequest(from_name=from_path, to_name=to_path))
        text = json.dumps(MessageToDict(resp), indent=2)
        return _tool_result(ctx, "move_file", req_args, text)
    except ConnectError as err:
        return _tool_error(ctx, "move_file", req_args, err)


def done(
    ctx: AgentRuntimeContext,
    message: str,
    outcome: str,
    refs: List[str] | None = None,
) -> str:
    """Submit final answer. Must be called exactly once."""
    refs = refs or []
    refs = [ref.lstrip("/") for ref in refs if isinstance(ref, str)]
    outcome_proto = OUTCOME_BY_NAME.get(outcome, Outcome.OUTCOME_OK)
    req_args = {
        "tool": "done",
        "message": message,
        "outcome": outcome,
        "refs": refs,
    }
    err = validate_or_error(ctx, "report_completion", req_args)
    if err:
        print(f"{CLI_RED}{err}{CLI_CLR}")
        return err
    try:
        ctx.vm.answer(AnswerRequest(
            message=message, outcome=outcome_proto, refs=refs
        ))
        ctx.pipeline.final_answer = message
        ctx.pipeline.final_code = outcome
        ctx.pipeline.harness_answer_submitted = True
        ctx.pipeline.loop_termination_reason = "report_completion"
        _tool_result(ctx, "done", req_args, f"{outcome}: {message}")
        print(f"{CLI_GREEN}Agent {outcome}{CLI_CLR}")
        print(f"  Message: {message}")
        if refs:
            print("  Refs:")
            for ref in refs:
                print(f"    - {ref}")
    except ConnectError as err:
        _tool_error(ctx, "done", req_args, err)
    return f"{outcome}: {message}"


# ── Tool schemas for OpenAI function calling ─────────────────────

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Read a file. Returns file content, or 'NOT_FOUND' if the file doesn't exist.\n\n"
                "Use this instead of browse() when you know the exact file path.\n"
                "Do NOT re-read files already shown in your context under [TRUSTED RULE] sections."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path to read"},
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browse",
            "description": "Browse the filesystem.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": 'Directory to browse (default: "/")'},
                    "mode": {
                        "type": "string",
                        "description": '"tree" (recursive), "ls" (direct children), "find" (search by filename)',
                    },
                    "name": {
                        "type": "string",
                        "description": 'Filename pattern for mode="find" (e.g. "*.md", "SR-13.json")',
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search",
            "description": "Search file contents using a regex or substring pattern.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Regex or substring to search for inside file contents",
                    },
                    "root": {"type": "string", "description": 'Directory to search under (default: "/")'},
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of matching files to return",
                    },
                },
                "required": ["pattern"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": (
                "Write content to a file. Creates parent directories if needed.\n"
                "Returns verified content length on success, or an error message."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path to write to"},
                    "content": {"type": "string", "description": "Full file content to write"},
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_file",
            "description": "Delete a file or directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File or directory path to delete"},
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "move_file",
            "description": "Move or rename a file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "from_path": {"type": "string", "description": "Current file path"},
                    "to_path": {"type": "string", "description": "New file path"},
                },
                "required": ["from_path", "to_path"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "done",
            "description": "Submit final answer. Must be called exactly once.",
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "What you did or why you can't. Be specific.",
                    },
                    "outcome": {
                        "type": "string",
                        "description": (
                            "OUTCOME_OK, OUTCOME_DENIED_SECURITY, OUTCOME_NONE_CLARIFICATION, "
                            "OUTCOME_NONE_UNSUPPORTED, or OUTCOME_ERR_INTERNAL"
                        ),
                    },
                    "refs": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Files you read/wrote/deleted (no leading /)",
                    },
                },
                "required": ["message", "outcome"],
                "additionalProperties": False,
            },
        },
    },
]


# ── Dispatcher ────────────────────────────────────────────────────

_TOOL_DISPATCH = {
    "read_file": read_file,
    "browse": browse,
    "search": search,
    "write_file": write_file,
    "delete_file": delete_file,
    "move_file": move_file,
    "done": done,
}


def dispatch_tool(name: str, arguments: dict, ctx: AgentRuntimeContext) -> str:
    """Dispatch a tool call by name, passing parsed arguments to the tool function."""
    fn = _TOOL_DISPATCH.get(name)
    if fn is None:
        return f"ERROR: Unknown tool '{name}'"
    return fn(ctx, **arguments)
