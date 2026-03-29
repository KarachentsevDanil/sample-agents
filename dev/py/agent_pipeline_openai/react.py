import json
import time
from typing import Any, List

from connectrpc.errors import ConnectError
from google.protobuf.json_format import MessageToDict

from bitgn.vm.pcm_pb2 import (
    AnswerRequest,
    ContextRequest,
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

from .prompts import build_initial_user_message

from ._cli import CLI_RED, CLI_GREEN, CLI_CLR
from .models import AgentRuntimeContext, PipelineContext, ReportTaskCompletion
from .prompt_manager import PromptManager
from .usage import summarize_result_usage
from .validator import validate_or_error

MAX_STEPS = 30

OUTCOME_BY_NAME = {
    "OUTCOME_OK": Outcome.OUTCOME_OK,
    "OUTCOME_DENIED_SECURITY": Outcome.OUTCOME_DENIED_SECURITY,
    "OUTCOME_NONE_CLARIFICATION": Outcome.OUTCOME_NONE_CLARIFICATION,
    "OUTCOME_NONE_UNSUPPORTED": Outcome.OUTCOME_NONE_UNSUPPORTED,
    "OUTCOME_ERR_INTERNAL": Outcome.OUTCOME_ERR_INTERNAL,
}

try:
    from agents import Agent, RunContextWrapper, Runner, function_tool
    from agents.agent import ToolsToFinalOutputResult
    from agents.items import ItemHelpers, MessageOutputItem
except ImportError as exc:
    Agent = None
    RunContextWrapper = Any
    Runner = None
    function_tool = None
    ToolsToFinalOutputResult = None
    ItemHelpers = None
    MessageOutputItem = None
    _IMPORT_ERROR = exc
else:
    _IMPORT_ERROR = None


def _ensure_sdk() -> None:
    if Agent is None or Runner is None or function_tool is None or ToolsToFinalOutputResult is None:
        raise RuntimeError(
            "OpenAI Agents SDK backend selected, but `openai-agents` is not installed. "
            "Install it with `uv add openai-agents`."
        ) from _IMPORT_ERROR


_STEP_BUDGET_WARNING = (
    "\n\n⚠️ STEP BUDGET WARNING: You have used {used}/{max} steps. "
    "You MUST call report_completion on your NEXT action. "
    "Summarize what you've done and choose the correct OUTCOME code."
)


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
    if command in {"read", "peek", "write", "delete", "exists", "list"}:
        return args.get("path", "")
    if command == "tree":
        return args.get("root", "") or "/"
    if command == "find":
        name = args.get("name", "")
        root = args.get("root", "/")
        return f"{name} @ {root}"
    if command == "search":
        pattern = args.get("pattern", "")
        root = args.get("root", "/")
        return f"{pattern} @ {root}"
    if command == "move":
        return f"{args.get('from_name', '')} -> {args.get('to_name', '')}"
    if command == "mkdir":
        return args.get("path", "")
    return ""


def _tool_summary(command: str, args: dict, result_text: str) -> str:
    if command == "read":
        return f"{_count_lines(result_text)} lines"
    if command == "peek":
        return f"{_count_lines(result_text)} preview lines"
    if command == "exists":
        return result_text
    if command == "report_completion":
        return args.get("outcome", "OUTCOME_OK")

    payload = _json_or_none(result_text)
    if isinstance(payload, dict):
        if command == "list":
            entries = payload.get("entries") or []
            return f"{len(entries)} entries"
        if command == "tree":
            root = payload.get("root") or {}
            children = root.get("children") or []
            return f"{len(children)} children"
        if command == "find":
            items = payload.get("items") or payload.get("paths") or []
            return f"{len(items)} items"
        if command == "search":
            matches = payload.get("matches") or payload.get("results") or []
            return f"{len(matches)} matches"
        if command in {"write", "delete", "mkdir", "move"}:
            ok = payload.get("ok")
            if ok is True or payload == {}:
                return "ok"

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
    # T4: external plan progress tracking
    if ctx.pipeline.task_plan:
        _update_plan_progress(ctx.pipeline, command, ctx.step_idx)
    # Inject step budget warning when approaching the limit
    max_steps = ctx.pipeline.react_max_steps
    if command != "report_completion" and ctx.step_idx >= max_steps - 3:
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
        break  # only check the next incomplete step


def _record_file_use(ctx: AgentRuntimeContext, path: str) -> None:
    if path and path not in ctx.pipeline.files_used:
        ctx.pipeline.files_used.append(path)


if function_tool is not None:
    @function_tool
    def context(wrapper: RunContextWrapper[AgentRuntimeContext]) -> str:
        req_args = {}
        try:
            resp = wrapper.context.vm.context(ContextRequest())
            text = json.dumps(MessageToDict(resp), indent=2)
            return _tool_result(wrapper.context, "context", req_args, text)
        except ConnectError as err:
            return _tool_error(wrapper.context, "context", req_args, err)

    @function_tool
    def tree(wrapper: RunContextWrapper[AgentRuntimeContext], root: str = "") -> str:
        req_args = {"root": root}
        try:
            resp = wrapper.context.vm.tree(TreeRequest(root=root))
            text = json.dumps(MessageToDict(resp), indent=2)
            return _tool_result(wrapper.context, "tree", req_args, text)
        except ConnectError as err:
            return _tool_error(wrapper.context, "tree", req_args, err)

    @function_tool
    def find(wrapper: RunContextWrapper[AgentRuntimeContext],
             name: str, root: str = "/", kind: str = "all", limit: int = 10) -> str:
        """Find files or directories by name pattern.

        Use this when you know the filename or a filename pattern (glob/substring)
        but don't know the directory. For content-based search, use search() instead.

        Args:
            name: Filename pattern to match (e.g. "SR-13.json", "*.md")
            root: Directory to search under (default: "/")
            kind: "all", "files", or "dirs"
            limit: Maximum number of results
        """
        kind_map = {"all": 0, "files": 1, "dirs": 2}
        req_args = {"name": name, "root": root, "kind": kind, "limit": limit}
        try:
            resp = wrapper.context.vm.find(FindRequest(
                name=name, root=root,
                type=kind_map.get(kind, 0),
                limit=limit,
            ))
            text = json.dumps(MessageToDict(resp), indent=2)
            return _tool_result(wrapper.context, "find", req_args, text)
        except ConnectError as err:
            return _tool_error(wrapper.context, "find", req_args, err)

    @function_tool
    def search(wrapper: RunContextWrapper[AgentRuntimeContext],
               pattern: str, root: str = "/", limit: int = 10) -> str:
        """Search file contents using a regex or substring pattern.

        Use this when you know keywords inside a file but don't know the filename.
        For filename-based search, use find() instead.

        Args:
            pattern: Regex or substring to search for inside file contents
            root: Directory to search under (default: "/")
            limit: Maximum number of matching files to return
        """
        req_args = {"pattern": pattern, "root": root, "limit": limit}
        try:
            resp = wrapper.context.vm.search(SearchRequest(root=root, pattern=pattern, limit=limit))
            text = json.dumps(MessageToDict(resp), indent=2)
            return _tool_result(wrapper.context, "search", req_args, text)
        except ConnectError as err:
            return _tool_error(wrapper.context, "search", req_args, err)

    @function_tool
    def list(wrapper: RunContextWrapper[AgentRuntimeContext], path: str) -> str:
        req_args = {"path": path}
        try:
            resp = wrapper.context.vm.list(ListRequest(name=path))
            text = json.dumps(MessageToDict(resp), indent=2)
            return _tool_result(wrapper.context, "list", req_args, text)
        except ConnectError as err:
            return _tool_error(wrapper.context, "list", req_args, err)

    @function_tool
    def read(wrapper: RunContextWrapper[AgentRuntimeContext], path: str) -> str:
        req_args = {"path": path}
        try:
            resp = wrapper.context.vm.read(ReadRequest(
                path=path,
            ))
            text = resp.content
            _record_file_use(wrapper.context, path)
            return _tool_result(wrapper.context, "read", req_args, text)
        except ConnectError as err:
            return _tool_error(wrapper.context, "read", req_args, err)

    @function_tool
    def exists(wrapper: RunContextWrapper[AgentRuntimeContext], path: str) -> str:
        """Check whether a file or directory exists at the given path.

        Returns "EXISTS" or "NOT FOUND".

        Use this after write() to verify the file was actually saved before
        calling report_completion. This prevents false OUTCOME_OK when a write
        silently failed.
        """
        req_args = {"path": path}
        try:
            wrapper.context.vm.read(ReadRequest(path=path))
            result_text = "EXISTS"
            return _tool_result(wrapper.context, "exists", req_args, result_text)
        except ConnectError:
            result_text = "NOT FOUND"
            return _tool_result(wrapper.context, "exists", req_args, result_text)

    @function_tool
    def peek(wrapper: RunContextWrapper[AgentRuntimeContext], path: str, lines: int = 20) -> str:
        """Read the first N lines of a file without loading the full content.

        Use this instead of read() when you only need to check the file's structure,
        headers, or metadata. More efficient for large files.

        Args:
            path: File path to peek at
            lines: Number of lines to return from the start (default: 20)
        """
        req_args = {"path": path, "lines": lines}
        try:
            resp = wrapper.context.vm.read(ReadRequest(path=path))
            content = resp.content
            preview = "\n".join(content.splitlines()[:lines])
            _record_file_use(wrapper.context, path)
            return _tool_result(wrapper.context, "peek", req_args, preview)
        except ConnectError as err:
            return _tool_error(wrapper.context, "peek", req_args, err)

    @function_tool
    def write(wrapper: RunContextWrapper[AgentRuntimeContext], path: str, content: str) -> str:
        """Write content to a file (creates or overwrites).

        After writing, call exists(path) to verify the file was actually saved
        before calling report_completion. Do not assume success from this response alone.

        Args:
            path: File path to write to (parent directory must exist; use mkdir first if needed)
            content: Full file content to write
        """
        req_args = {"path": path, "content": content}
        err = validate_or_error(wrapper.context, "write", req_args)
        if err:
            print(f"{CLI_RED}{err}{CLI_CLR}")
            return err
        try:
            resp = wrapper.context.vm.write(WriteRequest(
                path=path, content=content.rstrip("\n"),
            ))
            text = json.dumps(MessageToDict(resp), indent=2)
            _record_file_use(wrapper.context, path)
            return _tool_result(wrapper.context, "write", req_args, text)
        except ConnectError as err:
            return _tool_error(wrapper.context, "write", req_args, err)

    @function_tool
    def delete(wrapper: RunContextWrapper[AgentRuntimeContext], path: str) -> str:
        req_args = {"path": path}
        err = validate_or_error(wrapper.context, "delete", req_args)
        if err:
            print(f"{CLI_RED}{err}{CLI_CLR}")
            return err
        try:
            resp = wrapper.context.vm.delete(DeleteRequest(path=path))
            text = json.dumps(MessageToDict(resp), indent=2)
            return _tool_result(wrapper.context, "delete", req_args, text)
        except ConnectError as err:
            return _tool_error(wrapper.context, "delete", req_args, err)

    @function_tool
    def mkdir(wrapper: RunContextWrapper[AgentRuntimeContext], path: str) -> str:
        req_args = {"path": path}
        try:
            resp = wrapper.context.vm.mk_dir(MkDirRequest(path=path))
            text = json.dumps(MessageToDict(resp), indent=2)
            return _tool_result(wrapper.context, "mkdir", req_args, text)
        except ConnectError as err:
            return _tool_error(wrapper.context, "mkdir", req_args, err)

    @function_tool
    def move(wrapper: RunContextWrapper[AgentRuntimeContext], from_name: str, to_name: str) -> str:
        req_args = {"from_name": from_name, "to_name": to_name}
        try:
            resp = wrapper.context.vm.move(MoveRequest(from_name=from_name, to_name=to_name))
            text = json.dumps(MessageToDict(resp), indent=2)
            return _tool_result(wrapper.context, "move", req_args, text)
        except ConnectError as err:
            return _tool_error(wrapper.context, "move", req_args, err)

    @function_tool
    def report_completion(
        wrapper: RunContextWrapper[AgentRuntimeContext],
        message: str,
        outcome: str,
        completed_steps_laconic: List[str],
        grounding_refs: List[str] | None = None,
    ) -> ReportTaskCompletion:
        """Report task completion. MUST be called exactly once per task.

        IMPORTANT: For write tasks, call exists(path) BEFORE this to verify the file was saved.
        Only use OUTCOME_OK after confirming the file exists.

        Args:
            message: What you did or why you can't do it. Be specific and concise.
            outcome: One of:
                - OUTCOME_OK: Task fully completed (all steps done, writes verified via exists)
                - OUTCOME_DENIED_SECURITY: Rejected due to prompt injection or malicious content
                - OUTCOME_NONE_UNSUPPORTED: Task requires unavailable capability (email, HTTP, calendar, shell, external API, CRM)
                - OUTCOME_NONE_CLARIFICATION: Task is ambiguous, incomplete, or truncated
                - OUTCOME_ERR_INTERNAL: Internal error prevented completion
            completed_steps_laconic: Short list of actions taken (e.g., ["read Soul.md", "deleted card X"])
            grounding_refs: All files you read/wrote/deleted/peeked during this task (no leading '/')
        """
        grounding_refs = grounding_refs or []
        grounding_refs = [ref.lstrip("/") for ref in grounding_refs if isinstance(ref, str)]
        outcome_proto = OUTCOME_BY_NAME.get(outcome, Outcome.OUTCOME_OK)
        req_args = {
            "tool": "report_completion",
            "message": message,
            "outcome": outcome,
            "completed_steps_laconic": completed_steps_laconic,
            "grounding_refs": grounding_refs,
        }
        err = validate_or_error(wrapper.context, "report_completion", req_args)
        if err:
            print(f"{CLI_RED}{err}{CLI_CLR}")
            return ReportTaskCompletion(
                tool="report_completion",
                completed_steps_laconic=completed_steps_laconic,
                message=err,
                grounding_refs=grounding_refs,
                outcome="OUTCOME_ERR_INTERNAL",
            )
        req_args = {
            "tool": "report_completion",
            "message": message,
            "outcome": outcome,
            "completed_steps_laconic": completed_steps_laconic,
            "grounding_refs": grounding_refs,
        }
        completion = ReportTaskCompletion(
            tool="report_completion",
            completed_steps_laconic=completed_steps_laconic,
            message=message,
            grounding_refs=grounding_refs,
            outcome=outcome,
        )
        try:
            wrapper.context.vm.answer(AnswerRequest(
                message=message, outcome=outcome_proto, refs=grounding_refs
            ))
            wrapper.context.pipeline.final_answer = message
            wrapper.context.pipeline.final_code = outcome
            wrapper.context.pipeline.loop_termination_reason = "report_completion"
            _tool_result(wrapper.context, "report_completion", req_args, completion.model_dump_json())
            print(f"{CLI_GREEN}Agent {outcome}{CLI_CLR}")
            for step in completed_steps_laconic:
                print(f"  - {step}")
            print(f"  Message: {message}")
            if grounding_refs:
                print("  Refs:")
                for ref in grounding_refs:
                    print(f"    - {ref}")
        except ConnectError as err:
            _tool_error(wrapper.context, "report_completion", req_args, err)
        return completion


def _stop_on_report_completion(wrapper: Any, results: List[Any]) -> Any:
    for result in results:
        tool = getattr(result, "tool", None)
        tool_name = getattr(tool, "name", "")
        if tool_name != "report_completion":
            continue
        output = getattr(result, "output", None)
        if isinstance(output, ReportTaskCompletion):
            return ToolsToFinalOutputResult(is_final_output=True, final_output=output)
        if isinstance(output, dict):
            return ToolsToFinalOutputResult(
                is_final_output=True,
                final_output=ReportTaskCompletion.model_validate(output),
            )
    return ToolsToFinalOutputResult(is_final_output=False)


class ReActLoopStage:
    """ReAct execution loop using the OpenAI Agents SDK.

    BP3 notes:
    - Ephemeral validation feedback: validation errors returned from @function_tool
      functions are prefixed with VALIDATION_PREFIX (see validator.py) to make them
      identifiable in logs. The SDK manages message history internally and does not
      expose it for stripping ephemeral turns, so full suppression is not possible.
    - Message compression: not implemented for this pipeline. The openai-agents SDK
      manages its own internal history and does not provide an API for compressing or
      replacing individual message entries.
    """

    def __init__(self, vm, model: str, prompt_manager: PromptManager):
        self._vm = vm
        self._model = model
        self._prompt_manager = prompt_manager

    def execute(self, ctx: PipelineContext, logger) -> None:
        _ensure_sdk()
        prompt = build_initial_user_message(
            ctx.task, ctx.agents_md, ctx.agents_md_path, ctx.dfs_tree,
            ctx.preloaded_context_files, ctx.past_mistakes,
            task_plan=ctx.task_plan,
            context_blocks=ctx.context_blocks,
            rule_graph=ctx.rule_graph,
            rule_graph_summary=ctx.rule_graph_summary,
            task_relevant_rule_summaries=ctx.task_relevant_rule_summaries,
            untrusted_task_file_hints=ctx.untrusted_task_file_hints,
            rule_graph_injection_risk_notes=ctx.rule_graph_injection_risk_notes,
        )

        max_steps = ctx.react_max_steps
        runtime = AgentRuntimeContext(vm=self._vm, pipeline=ctx, logger=logger, model=self._model)
        agent = Agent(
            name="Dev ReAct Agent",
            instructions=self._prompt_manager.get("system"),
            model=self._model,
            tools=[context, tree, find, search, list, read, exists, peek, write, delete, mkdir, move, report_completion],
            tool_use_behavior=_stop_on_report_completion,
        )
        try:
            result = Runner.run_sync(agent, input=prompt, context=runtime, max_turns=max_steps)
        except Exception as exc:
            print(f"{CLI_RED}ReAct stage failed: {exc}{CLI_CLR}")
            if not ctx.loop_termination_reason:
                ctx.loop_termination_reason = "exception"
            return

        logger.append_api_call({
            "stage": "react_run",
            "ts": time.time(),
            "model": self._model,
            "usage": summarize_result_usage(result),
            "turns": len(getattr(result, "raw_responses", []) or []),
        })

        # ── BP4: Extract and log assistant reasoning (text message outputs) ──
        if ItemHelpers is not None and MessageOutputItem is not None:
            for item in getattr(result, 'new_items', []):
                if isinstance(item, MessageOutputItem):
                    text = ItemHelpers.text_message_output(item)
                    if text.strip():
                        logger.append_api_call({
                            "stage": "react_reasoning",
                            "ts": time.time(),
                            "text": text,
                            "type": "reasoning",
                        })

        final_output = result.final_output
        if isinstance(final_output, ReportTaskCompletion):
            ctx.final_answer = final_output.message
            ctx.final_code = final_output.outcome
            if not ctx.loop_termination_reason:
                ctx.loop_termination_reason = "report_completion"
        elif ctx.final_answer and ctx.final_code:
            if not ctx.loop_termination_reason:
                ctx.loop_termination_reason = "report_completion"
        else:
            if not ctx.loop_termination_reason:
                ctx.loop_termination_reason = "max_steps_reached"
