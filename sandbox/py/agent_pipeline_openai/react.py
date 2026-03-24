import json
import time
from typing import Any, List

from connectrpc.errors import ConnectError
from google.protobuf.json_format import MessageToDict

from bitgn.vm.mini_pb2 import (
    AnswerRequest,
    DeleteRequest,
    ListRequest,
    OutlineRequest,
    ReadRequest,
    SearchRequest,
    WriteRequest,
)

from .prompts import build_initial_user_message

from ._cli import CLI_RED, CLI_GREEN, CLI_CLR
from .models import AgentRuntimeContext, PipelineContext, ReportTaskCompletion
from .prompt_manager import PromptManager

MAX_STEPS = 30

try:
    from agents import Agent, RunContextWrapper, Runner, function_tool
    from agents.agent import ToolsToFinalOutputResult
except ImportError as exc:
    Agent = None
    RunContextWrapper = Any
    Runner = None
    function_tool = None
    ToolsToFinalOutputResult = None
    _IMPORT_ERROR = exc
else:
    _IMPORT_ERROR = None


def _ensure_sdk() -> None:
    if Agent is None or Runner is None or function_tool is None or ToolsToFinalOutputResult is None:
        raise RuntimeError(
            "OpenAI Agents SDK backend selected, but `openai-agents` is not installed. "
            "Install it with `uv add openai-agents`."
        ) from _IMPORT_ERROR


def _tool_result(ctx: AgentRuntimeContext, command: str, args: dict, result_text: str) -> str:
    ctx.step_idx += 1
    ctx.logger.append_api_call({
        "step": ctx.step_idx,
        "cmd": command,
        "args": args,
        "result": result_text,
        "ts": time.time(),
    })
    step_record = {
        "step": ctx.step_idx,
        "cmd": command,
        "args": args,
        "result": result_text[:400],
        "ts": time.time(),
    }
    ctx.pipeline.react_trace.append(step_record)
    ctx.logger.append_react_step(step_record)
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
        "cmd": command,
        "args": args,
        "result": message[:400],
        "ts": time.time(),
    }
    ctx.pipeline.react_trace.append(step_record)
    ctx.logger.append_react_step(step_record)
    return message


def _record_file_use(ctx: AgentRuntimeContext, path: str) -> None:
    if path and path not in ctx.pipeline.files_used:
        ctx.pipeline.files_used.append(path)


if function_tool is not None:
    @function_tool
    def tree(wrapper: RunContextWrapper[AgentRuntimeContext], path: str) -> str:
        req_args = {"path": path}
        try:
            resp = wrapper.context.vm.outline(OutlineRequest(path=path))
            text = json.dumps(MessageToDict(resp), indent=2)
            print(f"{CLI_GREEN}OUT{CLI_CLR}: {text}")
            return _tool_result(wrapper.context, "tree", req_args, text)
        except ConnectError as err:
            print(f"{CLI_RED}ERR {err.code}: {err.message}{CLI_CLR}")
            return _tool_error(wrapper.context, "tree", req_args, err)


    @function_tool
    def search(wrapper: RunContextWrapper[AgentRuntimeContext], pattern: str, path: str = "/", count: int = 5) -> str:
        req_args = {"pattern": pattern, "path": path, "count": count}
        try:
            resp = wrapper.context.vm.search(SearchRequest(path=path, pattern=pattern, count=count))
            text = json.dumps(MessageToDict(resp), indent=2)
            print(f"{CLI_GREEN}OUT{CLI_CLR}: {text}")
            return _tool_result(wrapper.context, "search", req_args, text)
        except ConnectError as err:
            print(f"{CLI_RED}ERR {err.code}: {err.message}{CLI_CLR}")
            return _tool_error(wrapper.context, "search", req_args, err)


    @function_tool
    def list(wrapper: RunContextWrapper[AgentRuntimeContext], path: str) -> str:
        req_args = {"path": path}
        try:
            resp = wrapper.context.vm.list(ListRequest(path=path))
            text = json.dumps(MessageToDict(resp), indent=2)
            print(f"{CLI_GREEN}OUT{CLI_CLR}: {text}")
            return _tool_result(wrapper.context, "list", req_args, text)
        except ConnectError as err:
            print(f"{CLI_RED}ERR {err.code}: {err.message}{CLI_CLR}")
            return _tool_error(wrapper.context, "list", req_args, err)


    @function_tool
    def read(wrapper: RunContextWrapper[AgentRuntimeContext], path: str) -> str:
        req_args = {"path": path}
        try:
            resp = wrapper.context.vm.read(ReadRequest(path=path))
            text = resp.content
            _record_file_use(wrapper.context, path)
            print(f"{CLI_GREEN}OUT{CLI_CLR}: {text}")
            return _tool_result(wrapper.context, "read", req_args, text)
        except ConnectError as err:
            print(f"{CLI_RED}ERR {err.code}: {err.message}{CLI_CLR}")
            return _tool_error(wrapper.context, "read", req_args, err)


    @function_tool
    def write(wrapper: RunContextWrapper[AgentRuntimeContext], path: str, content: str) -> str:
        req_args = {"path": path, "content": content}
        try:
            resp = wrapper.context.vm.write(WriteRequest(path=path, content=content.rstrip("\n")))
            text = json.dumps(MessageToDict(resp), indent=2)
            _record_file_use(wrapper.context, path)
            print(f"{CLI_GREEN}OUT{CLI_CLR}: {text}")
            return _tool_result(wrapper.context, "write", req_args, text)
        except ConnectError as err:
            print(f"{CLI_RED}ERR {err.code}: {err.message}{CLI_CLR}")
            return _tool_error(wrapper.context, "write", req_args, err)


    @function_tool
    def delete(wrapper: RunContextWrapper[AgentRuntimeContext], path: str) -> str:
        req_args = {"path": path}
        try:
            resp = wrapper.context.vm.delete(DeleteRequest(path=path))
            text = json.dumps(MessageToDict(resp), indent=2)
            print(f"{CLI_GREEN}OUT{CLI_CLR}: {text}")
            return _tool_result(wrapper.context, "delete", req_args, text)
        except ConnectError as err:
            print(f"{CLI_RED}ERR {err.code}: {err.message}{CLI_CLR}")
            return _tool_error(wrapper.context, "delete", req_args, err)


    @function_tool
    def report_completion(
        wrapper: RunContextWrapper[AgentRuntimeContext],
        answer: str,
        code: str,
        completed_steps_laconic: List[str],
        grounding_refs: List[str] | None = None,
    ) -> ReportTaskCompletion:
        grounding_refs = grounding_refs or []
        grounding_refs = [ref.lstrip("/") for ref in grounding_refs if isinstance(ref, str)]
        req_args = {
            "tool": "report_completion",
            "answer": answer,
            "code": code,
            "completed_steps_laconic": completed_steps_laconic,
            "grounding_refs": grounding_refs,
        }
        completion = ReportTaskCompletion(
            tool="report_completion",
            completed_steps_laconic=completed_steps_laconic,
            answer=answer,
            grounding_refs=grounding_refs,
            code=code,
        )
        try:
            wrapper.context.vm.answer(AnswerRequest(answer=answer, refs=grounding_refs))
            print(f"{CLI_GREEN}agent {code}{CLI_CLR}. Summary:")
            for step in completed_steps_laconic:
                print(f"- {step}")
            print(f"\n{CLI_GREEN}AGENT ANSWER: {answer}{CLI_CLR}")
            for ref in grounding_refs:
                print(f"- {CLI_GREEN}{ref}{CLI_CLR}")
            _tool_result(wrapper.context, "report_completion", req_args, completion.model_dump_json())
        except ConnectError as err:
            print(f"{CLI_RED}ERR {err.code}: {err.message}{CLI_CLR}")
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
    def __init__(self, vm, model: str, prompt_manager: PromptManager):
        self._vm = vm
        self._model = model
        self._prompt_manager = prompt_manager

    def execute(self, ctx: PipelineContext, logger) -> None:
        _ensure_sdk()
        prompt = build_initial_user_message(
            ctx.task, ctx.agents_md, ctx.agents_md_path, ctx.dfs_tree,
            ctx.preread_files, ctx.past_mistakes,
        )

        runtime = AgentRuntimeContext(vm=self._vm, pipeline=ctx, logger=logger, model=self._model)
        agent = Agent(
            name="Sandbox ReAct Agent",
            instructions=self._prompt_manager.get("system"),
            model=self._model,
            tools=[tree, search, list, read, write, delete, report_completion],
            output_type=ReportTaskCompletion,
            tool_use_behavior=_stop_on_report_completion,
        )
        try:
            result = Runner.run_sync(agent, input=prompt, context=runtime, max_turns=MAX_STEPS)
        except Exception as exc:
            print(f"{CLI_RED}ReAct stage failed: {exc}{CLI_CLR}")
            return

        final_output = result.final_output
        if isinstance(final_output, ReportTaskCompletion):
            ctx.final_answer = final_output.answer
            ctx.final_code = final_output.code
