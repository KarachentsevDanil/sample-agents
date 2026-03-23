import json
import time

from connectrpc.errors import ConnectError
from google.protobuf.json_format import MessageToDict
from langchain_core.messages import AIMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langgraph.runtime import Runtime

from bitgn.vm.mini_pb2 import (
    AnswerRequest,
    DeleteRequest,
    ListRequest,
    OutlineRequest,
    ReadRequest,
    SearchRequest,
    WriteRequest,
)

from agent_pipeline.models import VerificationResult
from agent_pipeline.prompts import VERIFIER_PROMPT
from openai_config import OPENAI_API_KEY

from .state import AgentState, GraphContext
from .tools import ALL_TOOLS

MAX_STEPS = 30
MAX_VERIFY_RETRIES = 2

CLI_RED = "\x1B[31m"
CLI_GREEN = "\x1B[32m"
CLI_CLR = "\x1B[0m"


def react_agent_node(state: AgentState, runtime: Runtime[GraphContext]) -> dict:
    model = state["model"]
    llm = ChatOpenAI(model=model, max_tokens=16384, api_key=OPENAI_API_KEY).bind_tools(ALL_TOOLS)
    response = llm.invoke(state["messages"])

    step_count = state.get("step_count", 0)
    print(f"Next step_{step_count + 1}... ", end="")
    if hasattr(response, "tool_calls") and response.tool_calls:
        tc = response.tool_calls[0]
        print(tc["name"], str(tc["args"])[:100])
    else:
        print("(no tool call — routing to verifier)")

    return {
        "messages": [response],
        "step_count": step_count + 1,
    }


def react_tools_node(state: AgentState, runtime: Runtime[GraphContext]) -> dict:
    vm = runtime.context.vm
    client = runtime.context.client
    logger = runtime.context.logger
    model = state["model"]
    step_count = state.get("step_count", 0)

    last_msg: AIMessage = state["messages"][-1]
    tool_messages = []
    react_trace_additions = []
    files_used_additions = []
    state_updates: dict = {}

    for tc in last_msg.tool_calls:
        tool_name = tc["name"]
        args = tc["args"]
        tool_call_id = tc["id"]
        result_str = ""
        log_this_call = True

        try:
            if tool_name == "tree":
                result = vm.outline(OutlineRequest(path=args["path"]))
                result_str = json.dumps(MessageToDict(result), indent=2)

            elif tool_name == "search":
                result = vm.search(SearchRequest(
                    path=args.get("path", "/"),
                    pattern=args["pattern"],
                    count=args.get("count", 5),
                ))
                result_str = json.dumps(MessageToDict(result), indent=2)

            elif tool_name == "list_dir":
                result = vm.list(ListRequest(path=args["path"]))
                result_str = json.dumps(MessageToDict(result), indent=2)

            elif tool_name == "read":
                result = vm.read(ReadRequest(path=args["path"]))
                result_str = json.dumps(MessageToDict(result), indent=2)
                files_used_additions.append(args["path"])

            elif tool_name == "write":
                result = vm.write(WriteRequest(path=args["path"], content=args["content"]))
                result_str = json.dumps(MessageToDict(result), indent=2)
                files_used_additions.append(args["path"])

            elif tool_name == "delete":
                result = vm.delete(DeleteRequest(path=args["path"]))
                result_str = json.dumps(MessageToDict(result), indent=2)

            elif tool_name == "report_completion":
                answer = args.get("answer", "")
                grounding_refs = args.get("grounding_refs", [])
                completed_steps = args.get("completed_steps_laconic", [])
                code = args.get("code", "completed")

                if not answer.strip():
                    result_str = "ERROR: answer is empty. Re-read relevant files and provide a complete non-empty answer."
                    tool_messages.append(ToolMessage(content=result_str, tool_call_id=tool_call_id))
                    log_this_call = False

                else:
                    inline_verify_attempts = state.get("inline_verify_attempts", 0)
                    should_submit = True
                    if inline_verify_attempts < MAX_VERIFY_RETRIES:
                        verdict = _inline_verify(client, model, state, answer, grounding_refs)
                        if verdict is not None and not verdict.passed:
                            state_updates["inline_verify_attempts"] = inline_verify_attempts + 1
                            result_str = (
                                f"Pre-submission check failed: {verdict.reason}\n"
                                "Re-examine the task requirements and AGENTS.md, then try again."
                            )
                            tool_messages.append(ToolMessage(content=result_str, tool_call_id=tool_call_id))
                            log_this_call = False
                            should_submit = False

                    if should_submit:
                        vm.answer(AnswerRequest(answer=answer, refs=grounding_refs))
                        state_updates["final_answer"] = answer
                        state_updates["final_code"] = code
                        result_str = json.dumps({"submitted": True, "answer": answer, "code": code})
                        print(f"{CLI_GREEN}agent {code}{CLI_CLR}. Steps:")
                        for s in completed_steps:
                            print(f"- {s}")
                        print(f"\n{CLI_GREEN}AGENT ANSWER: {answer}{CLI_CLR}")
                        if grounding_refs:
                            for ref in grounding_refs:
                                print(f"- {CLI_GREEN}{ref}{CLI_CLR}")

            else:
                result_str = f"ERROR: Unknown tool '{tool_name}'"

        except ConnectError as e:
            result_str = f"ERROR {e.code}: {e.message}"
            print(f"{CLI_RED}{result_str}{CLI_CLR}")

        if log_this_call:
            print(f"{CLI_GREEN}OUT{CLI_CLR}: {result_str[:200]}")

            api_record = {
                "step": step_count,
                "cmd": tool_name,
                "args": args,
                "result": result_str[:500],
                "ts": time.time(),
            }
            logger.append_api_call(api_record)

            step_record = {
                "step": step_count,
                "function": tool_name,
                "current_state": str(args)[:120],
                "plan": [],
                "result_summary": result_str[:400],
                "ts": time.time(),
            }
            react_trace_additions.append(step_record)
            logger.append_react_step(step_record)

            tool_messages.append(ToolMessage(content=result_str, tool_call_id=tool_call_id))

    updates = {
        "messages": tool_messages,
        "react_trace": state.get("react_trace", []) + react_trace_additions,
        "files_used": state.get("files_used", []) + files_used_additions,
    }
    updates.update(state_updates)
    return updates


def _inline_verify(
    client, model: str, state: AgentState, answer: str, grounding_refs: list
) -> VerificationResult | None:
    trace_summary = "\n".join(
        f"Step {s['step']}: {s['function']} — {s.get('result_summary', '')[:120]}"
        for s in state.get("react_trace", [])
    ) or "(no steps yet)"
    messages = [
        {"role": "system", "content": VERIFIER_PROMPT},
        {"role": "user", "content": (
            f"Task: {state['task']}\n\n"
            f"AGENTS.md instructions:\n{state.get('agents_md', '')}\n\n"
            f"Reasoning trace summary:\n{trace_summary}\n\n"
            f"Final answer: {answer}\n"
            f"Grounding refs: {grounding_refs}"
        )},
    ]
    try:
        resp = client.beta.chat.completions.parse(
            model=model,
            response_format=VerificationResult,
            messages=messages,
            max_completion_tokens=512,
        )
        return resp.choices[0].message.parsed
    except Exception:
        return None
