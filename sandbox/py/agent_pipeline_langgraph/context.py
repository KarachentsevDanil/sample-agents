import json

from connectrpc.errors import ConnectError
from google.protobuf.json_format import MessageToDict
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.runtime import Runtime

from bitgn.vm.mini_pb2 import OutlineRequest, ReadRequest

from agent_pipeline.models import FileSuggestion
from agent_pipeline.prompts import CONTEXT_SUGGESTION_PROMPT, SYSTEM_PROMPT, build_initial_user_message

from .state import AgentState, GraphContext

MAX_PREREAD_FILES = 8


def context_builder_node(state: AgentState, runtime: Runtime[GraphContext]) -> dict:
    vm = runtime.context.vm
    client = runtime.context.client
    logger = runtime.context.logger
    model = state["model"]
    task = state["task"]

    agents_md_path, agents_md = _fetch_agents_md(vm)
    dfs_tree = _fetch_dfs(vm)
    suggested = _suggest_files(client, model, task, agents_md, dfs_tree)
    preread_files = _read_files(vm, suggested)
    past_mistakes = logger.load_past_mistakes()

    initial_user_msg = build_initial_user_message(
        task, agents_md, agents_md_path, dfs_tree, preread_files, past_mistakes
    )
    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=initial_user_msg),
    ]

    return {
        "agents_md": agents_md,
        "agents_md_path": agents_md_path,
        "dfs_tree": dfs_tree,
        "preread_files": preread_files,
        "past_mistakes": past_mistakes,
        "messages": messages,
    }


def _fetch_agents_md(vm) -> tuple[str, str]:
    for path in ("AGENTS.md", "AGENTS.MD"):
        try:
            content = vm.read(ReadRequest(path=path)).content
            return path, content
        except ConnectError:
            continue
    return "", ""


def _fetch_dfs(vm) -> str:
    try:
        resp = vm.outline(OutlineRequest(path="/"))
        return json.dumps(MessageToDict(resp), indent=2)
    except ConnectError:
        return ""


def _suggest_files(client, model: str, task: str, agents_md: str, dfs_tree: str) -> list:
    if not dfs_tree:
        return []
    messages = [
        {"role": "system", "content": CONTEXT_SUGGESTION_PROMPT},
        {"role": "user", "content": (
            f"Task: {task}\n\nAGENTS.md:\n{agents_md}\n\nFilesystem:\n{dfs_tree}"
        )},
    ]
    try:
        resp = client.beta.chat.completions.parse(
            model=model,
            response_format=FileSuggestion,
            messages=messages,
            max_completion_tokens=1024,
        )
        result = resp.choices[0].message.parsed
        return (result.files_to_read or [])[:MAX_PREREAD_FILES]
    except Exception:
        return []


def _read_files(vm, paths: list) -> dict:
    out = {}
    for path in paths:
        try:
            out[path] = vm.read(ReadRequest(path=path)).content
        except ConnectError:
            continue
    return out
