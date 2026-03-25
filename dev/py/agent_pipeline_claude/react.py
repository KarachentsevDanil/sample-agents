import json
import time

import anthropic
from connectrpc.errors import ConnectError
from google.protobuf.json_format import MessageToDict

from bitgn.vm.pcm_connect import PcmRuntimeClientSync
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

from .models import PipelineContext
from .prompts import build_initial_user_message
from ._cli import CLI_RED, CLI_GREEN, CLI_CLR
from ._logging import build_api_log_entry
from .prompt_manager import PromptManager
from .tools import TOOLS

MAX_STEPS = 30

OUTCOME_BY_NAME = {
    "OUTCOME_OK": Outcome.OUTCOME_OK,
    "OUTCOME_DENIED_SECURITY": Outcome.OUTCOME_DENIED_SECURITY,
    "OUTCOME_NONE_CLARIFICATION": Outcome.OUTCOME_NONE_CLARIFICATION,
    "OUTCOME_NONE_UNSUPPORTED": Outcome.OUTCOME_NONE_UNSUPPORTED,
    "OUTCOME_ERR_INTERNAL": Outcome.OUTCOME_ERR_INTERNAL,
}


def _serialize_messages(messages: list) -> list:
    return [
        {**m, "content": [b.model_dump() if hasattr(b, "model_dump") else b for b in m["content"]]}
        if isinstance(m.get("content"), list) else m
        for m in messages
    ]


def _dispatch_tool(vm: PcmRuntimeClientSync, tool_name: str, tool_input: dict) -> str:
    try:
        if tool_name == "tree":
            return json.dumps(MessageToDict(vm.tree(TreeRequest(root=tool_input.get("root", "")))), indent=2)
        elif tool_name == "find":
            kind_map = {"all": 0, "files": 1, "dirs": 2}
            return json.dumps(MessageToDict(vm.find(FindRequest(
                name=tool_input["name"],
                root=tool_input.get("root", "/"),
                type=kind_map.get(tool_input.get("kind", "all"), 0),
                limit=tool_input.get("limit", 10),
            ))), indent=2)
        elif tool_name == "search":
            return json.dumps(MessageToDict(vm.search(SearchRequest(
                root=tool_input.get("root", "/"),
                pattern=tool_input["pattern"],
                limit=tool_input.get("limit", 10),
            ))), indent=2)
        elif tool_name == "list":
            return json.dumps(MessageToDict(vm.list(ListRequest(name=tool_input["path"]))), indent=2)
        elif tool_name == "read":
            return vm.read(ReadRequest(path=tool_input["path"])).content
        elif tool_name == "write":
            return json.dumps(MessageToDict(vm.write(WriteRequest(
                path=tool_input["path"], content=tool_input["content"].rstrip("\n")
            ))), indent=2)
        elif tool_name == "delete":
            return json.dumps(MessageToDict(vm.delete(DeleteRequest(path=tool_input["path"]))), indent=2)
        elif tool_name == "mkdir":
            return json.dumps(MessageToDict(vm.mk_dir(MkDirRequest(path=tool_input["path"]))), indent=2)
        elif tool_name == "move":
            return json.dumps(MessageToDict(vm.move(MoveRequest(
                from_name=tool_input["from_name"], to_name=tool_input["to_name"]
            ))), indent=2)
        else:
            return f"ERROR: unknown tool {tool_name}"
    except ConnectError as e:
        return f"ERROR {e.code}: {e.message}"


class ReActLoopStage:
    def __init__(self, vm: PcmRuntimeClientSync, client: anthropic.Anthropic, model: str,
                 prompt_manager: PromptManager):
        self._vm = vm
        self._client = client
        self._model = model
        self._prompt_manager = prompt_manager

    def execute(self, ctx: PipelineContext, logger) -> None:
        prompt = build_initial_user_message(
            ctx.task, ctx.agents_md, ctx.agents_md_path,
            ctx.dfs_tree, ctx.preread_files, ctx.past_mistakes,
        )
        messages = [{"role": "user", "content": prompt}]
        step_count = 0
        completed = False

        while step_count < MAX_STEPS:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=4096,
                system=self._prompt_manager.get("system"),
                tools=TOOLS,
                messages=messages,
            )

            logger.append_api_call(build_api_log_entry(
                "react", self._model, self._prompt_manager.get("system"),
                _serialize_messages(messages), response,
                step=step_count,
            ))
            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason != "tool_use":
                break

            tool_results = []

            for block in response.content:
                if block.type != "tool_use":
                    continue

                tool_name = block.name
                tool_input = block.input
                tool_use_id = block.id
                step_count += 1
                print(f"Step {step_count}: {tool_name}", end=" ")

                if tool_name == "report_completion":
                    message = (tool_input.get("message") or "").strip()
                    outcome_str = tool_input.get("outcome", "OUTCOME_OK")
                    grounding_refs = tool_input.get("grounding_refs") or []
                    if isinstance(grounding_refs, str):
                        try:
                            grounding_refs = json.loads(grounding_refs)
                        except Exception:
                            grounding_refs = []
                    grounding_refs = [r.lstrip("/") for r in grounding_refs if isinstance(r, str)]
                    outcome_proto = OUTCOME_BY_NAME.get(outcome_str, Outcome.OUTCOME_OK)

                    if not message:
                        result_text = "ERROR: message is empty. Re-read relevant files and provide a non-empty message."
                        print(f"{CLI_RED}empty message{CLI_CLR}")
                    else:
                        try:
                            self._vm.answer(AnswerRequest(
                                message=message, outcome=outcome_proto, refs=grounding_refs
                            ))
                            ctx.final_answer = message
                            ctx.final_code = outcome_str
                            result_text = f"Task submitted successfully. Outcome: {outcome_str}. Message: {message}"
                            completed = True
                            print(f"{CLI_GREEN}{message}{CLI_CLR}")
                            for ref in grounding_refs:
                                print(f"- {CLI_GREEN}{ref}{CLI_CLR}")
                        except ConnectError as e:
                            result_text = f"ERROR submitting answer {e.code}: {e.message}"
                            print(f"{CLI_RED}{result_text}{CLI_CLR}")

                    step_record = {
                        "step": step_count, "cmd": tool_name,
                        "args": tool_input, "result": result_text, "ts": time.time(),
                    }
                    ctx.react_trace.append(step_record)
                    logger.append_react_step(step_record)
                    tool_results.append({"type": "tool_result", "tool_use_id": tool_use_id, "content": result_text})
                    continue

                result_text = _dispatch_tool(self._vm, tool_name, tool_input)
                print(f"{CLI_GREEN}ok{CLI_CLR}")

                step_record = {
                    "step": step_count, "cmd": tool_name,
                    "args": tool_input, "result": result_text[:400], "ts": time.time(),
                }
                ctx.react_trace.append(step_record)
                logger.append_react_step(step_record)

                if tool_name in ("read", "write"):
                    path = tool_input.get("path")
                    if path and path not in ctx.files_used:
                        ctx.files_used.append(path)

                tool_results.append({"type": "tool_result", "tool_use_id": tool_use_id, "content": result_text})

            if tool_results:
                messages.append({"role": "user", "content": tool_results})

            if completed:
                break

        if not ctx.final_answer:
            print(f"{CLI_RED}No answer produced after {step_count} steps{CLI_CLR}")
