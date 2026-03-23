import json
from typing import Any

from connectrpc.errors import ConnectError
from google.protobuf.json_format import MessageToDict
from pydantic import ValidationError

from bitgn.vm.mini_pb2 import (
    AnswerRequest,
    DeleteRequest,
    ListRequest,
    OutlineRequest,
    ReadRequest,
    SearchRequest,
    WriteRequest,
)

from agent_pipeline.models import PipelineContext, VerificationResult
from agent_pipeline.prompts import SYSTEM_PROMPT, VERIFIER_PROMPT, build_initial_user_message
from openai_config import OPENAI_API_KEY

from .runtime import AgentRuntimeContext, record_file_use, record_tool_error, record_tool_success
from .tools import LANGCHAIN_TOOLS, ReportCompletionArgs, TOOL_SCHEMAS

MAX_STEPS = 30
MAX_VERIFY_RETRIES = 2

CLI_RED = "\x1B[31m"
CLI_GREEN = "\x1B[32m"
CLI_CLR = "\x1B[0m"

try:
    from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
    from langchain_openai import ChatOpenAI
except ImportError as exc:
    ChatOpenAI = None
    HumanMessage = None
    SystemMessage = None
    ToolMessage = None
    _IMPORT_ERROR = exc
else:
    _IMPORT_ERROR = None


def _ensure_langchain() -> None:
    if ChatOpenAI is None or HumanMessage is None or SystemMessage is None or ToolMessage is None:
        raise RuntimeError(
            "LangChain backend selected, but `langchain`/`langchain-openai` is not installed. "
            "Install them with the pinned versions in pyproject.toml."
        ) from _IMPORT_ERROR


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if text:
                    parts.append(str(text))
        return "\n".join(parts)
    return str(content or "")


class ReActLoopStage:
    def __init__(self, vm, model: str):
        self._vm = vm
        self._model = model
        self._verify_attempts = 0

    def execute(self, ctx: PipelineContext, logger) -> None:
        _ensure_langchain()
        self._verify_attempts = 0

        llm = ChatOpenAI(model=self._model, api_key=OPENAI_API_KEY)
        planner = llm.bind_tools(LANGCHAIN_TOOLS)
        verifier = ChatOpenAI(
            model=self._model,
            api_key=OPENAI_API_KEY,
        ).with_structured_output(VerificationResult, method="function_calling")

        prompt = build_initial_user_message(
            ctx.task, ctx.agents_md, ctx.agents_md_path, ctx.dfs_tree,
            ctx.preread_files, ctx.past_mistakes,
        )
        messages = [
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=prompt),
        ]
        runtime = AgentRuntimeContext(vm=self._vm, pipeline=ctx, logger=logger, model=self._model)

        for step_idx in range(MAX_STEPS):
            print(f"Next step_{step_idx + 1}... ", end="")

            try:
                response = planner.invoke(messages)
            except Exception as exc:
                print(f"{CLI_RED}ReAct stage failed: {exc}{CLI_CLR}")
                logger.append_llm_parse_error({
                    "attempt": step_idx + 1,
                    "error": str(exc),
                    "raw_content": None,
                })
                break

            messages.append(response)
            tool_calls = getattr(response, "tool_calls", None) or []

            if len(tool_calls) != 1:
                raw_content = _content_to_text(getattr(response, "content", ""))
                logger.append_llm_parse_error({
                    "attempt": step_idx + 1,
                    "error": f"Expected exactly one tool call, got {len(tool_calls)}",
                    "raw_content": raw_content,
                })
                print(f"{CLI_RED}Expected exactly one tool call, got {len(tool_calls)}{CLI_CLR}")

            if not tool_calls:
                messages.append(HumanMessage(content=(
                    "Your previous response was invalid. "
                    "You MUST call exactly one tool on every turn."
                )))
                continue

            tool_call = tool_calls[0]
            name = tool_call.get("name", "")
            args = tool_call.get("args") or {}
            call_id = tool_call.get("id", f"tool-call-{step_idx + 1}")
            print(args.get("reason", "") or f"Run {name}")

            validated = self._validate_tool_call(name, args, logger, step_idx + 1)
            if validated is None:
                messages.append(ToolMessage(
                    content=f"Invalid arguments for tool `{name}`. Fix the arguments and call exactly one tool.",
                    tool_call_id=call_id,
                ))
                for extra_tool_call in tool_calls[1:]:
                    messages.append(ToolMessage(
                        content=(
                            "Ignored because this pipeline allows exactly one tool call per turn. "
                            "Retry with a single tool call."
                        ),
                        tool_call_id=extra_tool_call.get("id", f"tool-call-extra-{step_idx + 1}"),
                    ))
                continue

            if name == "report_completion":
                if not validated.answer.strip():
                    messages.append(HumanMessage(content=(
                        "ERROR: answer is empty. Re-read relevant files and provide a complete non-empty answer."
                    )))
                    continue

                if self._verify_attempts < MAX_VERIFY_RETRIES:
                    verdict = self._inline_verify(ctx, verifier, validated)
                    if verdict is not None and not verdict.passed:
                        self._verify_attempts += 1
                        messages.append(ToolMessage(
                            content=(
                                f"Pre-submission check failed: {verdict.reason}\n"
                                "Re-examine the task requirements and AGENTS.md, then try again."
                            ),
                            tool_call_id=call_id,
                        ))
                        for extra_tool_call in tool_calls[1:]:
                            messages.append(ToolMessage(
                                content=(
                                    "Ignored because this pipeline allows exactly one tool call per turn. "
                                    "Retry with a single tool call."
                                ),
                                tool_call_id=extra_tool_call.get("id", f"tool-call-extra-{step_idx + 1}"),
                            ))
                        messages.append(HumanMessage(content=(
                            f"Pre-submission check failed: {verdict.reason}\n"
                            "Re-examine the task requirements and AGENTS.md, then try again."
                        )))
                        continue

            tool_output = self._dispatch(runtime, name, validated.model_dump())
            messages.append(ToolMessage(content=tool_output, tool_call_id=call_id))
            for extra_tool_call in tool_calls[1:]:
                messages.append(ToolMessage(
                    content=(
                        "Ignored because this pipeline allows exactly one tool call per turn. "
                        "Retry with a single tool call."
                    ),
                    tool_call_id=extra_tool_call.get("id", f"tool-call-extra-{step_idx + 1}"),
                ))

            if name == "report_completion":
                ctx.final_answer = validated.answer
                ctx.final_code = validated.code
                break

    def _validate_tool_call(self, name: str, args: dict, logger, attempt: int):
        schema = TOOL_SCHEMAS.get(name)
        if schema is None:
            logger.append_llm_parse_error({
                "attempt": attempt,
                "error": f"Unknown tool requested: {name}",
                "raw_content": json.dumps(args),
            })
            return None
        try:
            return schema.model_validate(args)
        except ValidationError as exc:
            logger.append_llm_parse_error({
                "attempt": attempt,
                "error": str(exc),
                "raw_content": json.dumps(args),
            })
            return None

    def _inline_verify(self, ctx: PipelineContext, verifier, completion: ReportCompletionArgs) -> VerificationResult | None:
        trace_summary = "\n".join(
            f"Step {s['step']}: {s['function']} — {s.get('result_summary', '')[:120]}"
            for s in ctx.react_trace
        ) or "(no steps yet)"
        try:
            result = verifier.invoke([
                SystemMessage(content=VERIFIER_PROMPT),
                HumanMessage(content=(
                    f"Task: {ctx.task}\n\n"
                    f"AGENTS.md instructions:\n{ctx.agents_md}\n\n"
                    f"Reasoning trace summary:\n{trace_summary}\n\n"
                    f"Final answer: {completion.answer}\n"
                    f"Grounding refs: {completion.grounding_refs}"
                )),
            ])
        except Exception:
            return None

        if isinstance(result, VerificationResult):
            return result
        if isinstance(result, dict):
            return VerificationResult.model_validate(result)
        return None

    def _dispatch(self, runtime: AgentRuntimeContext, name: str, args: dict) -> str:
        if name == "tree":
            try:
                resp = runtime.vm.outline(OutlineRequest(path=args["path"]))
                text = json.dumps(MessageToDict(resp), indent=2)
                print(f"{CLI_GREEN}OUT{CLI_CLR}: {text}")
                return record_tool_success(runtime, name, args, text)
            except ConnectError as err:
                print(f"{CLI_RED}ERR {err.code}: {err.message}{CLI_CLR}")
                return record_tool_error(runtime, name, args, err)

        if name == "search":
            try:
                resp = runtime.vm.search(SearchRequest(
                    path=args["path"],
                    pattern=args["pattern"],
                    count=args["count"],
                ))
                text = json.dumps(MessageToDict(resp), indent=2)
                print(f"{CLI_GREEN}OUT{CLI_CLR}: {text}")
                return record_tool_success(runtime, name, args, text)
            except ConnectError as err:
                print(f"{CLI_RED}ERR {err.code}: {err.message}{CLI_CLR}")
                return record_tool_error(runtime, name, args, err)

        if name == "list":
            try:
                resp = runtime.vm.list(ListRequest(path=args["path"]))
                text = json.dumps(MessageToDict(resp), indent=2)
                print(f"{CLI_GREEN}OUT{CLI_CLR}: {text}")
                return record_tool_success(runtime, name, args, text)
            except ConnectError as err:
                print(f"{CLI_RED}ERR {err.code}: {err.message}{CLI_CLR}")
                return record_tool_error(runtime, name, args, err)

        if name == "read":
            try:
                resp = runtime.vm.read(ReadRequest(path=args["path"]))
                text = resp.content
                record_file_use(runtime, args["path"])
                print(f"{CLI_GREEN}OUT{CLI_CLR}: {text}")
                return record_tool_success(runtime, name, args, text)
            except ConnectError as err:
                print(f"{CLI_RED}ERR {err.code}: {err.message}{CLI_CLR}")
                return record_tool_error(runtime, name, args, err)

        if name == "write":
            try:
                resp = runtime.vm.write(WriteRequest(path=args["path"], content=args["content"]))
                text = json.dumps(MessageToDict(resp), indent=2)
                record_file_use(runtime, args["path"])
                print(f"{CLI_GREEN}OUT{CLI_CLR}: {text}")
                return record_tool_success(runtime, name, args, text)
            except ConnectError as err:
                print(f"{CLI_RED}ERR {err.code}: {err.message}{CLI_CLR}")
                return record_tool_error(runtime, name, args, err)

        if name == "delete":
            try:
                resp = runtime.vm.delete(DeleteRequest(path=args["path"]))
                text = json.dumps(MessageToDict(resp), indent=2)
                record_file_use(runtime, args["path"])
                print(f"{CLI_GREEN}OUT{CLI_CLR}: {text}")
                return record_tool_success(runtime, name, args, text)
            except ConnectError as err:
                print(f"{CLI_RED}ERR {err.code}: {err.message}{CLI_CLR}")
                return record_tool_error(runtime, name, args, err)

        if name == "report_completion":
            try:
                runtime.vm.answer(AnswerRequest(answer=args["answer"], refs=args["grounding_refs"]))
                text = json.dumps({
                    "answer": args["answer"],
                    "code": args["code"],
                    "completed_steps_laconic": args["completed_steps_laconic"],
                    "grounding_refs": args["grounding_refs"],
                })
                print(f"{CLI_GREEN}agent {args['code']}{CLI_CLR}. Summary:")
                for step in args["completed_steps_laconic"]:
                    print(f"- {step}")
                print(f"\n{CLI_GREEN}AGENT ANSWER: {args['answer']}{CLI_CLR}")
                for ref in args["grounding_refs"]:
                    print(f"- {CLI_GREEN}{ref}{CLI_CLR}")
                return record_tool_success(runtime, name, args, text)
            except ConnectError as err:
                print(f"{CLI_RED}ERR {err.code}: {err.message}{CLI_CLR}")
                return record_tool_error(runtime, name, args, err)

        raise ValueError(f"Unknown tool: {name}")
