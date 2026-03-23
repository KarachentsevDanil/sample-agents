import json

from connectrpc.errors import ConnectError
from google.protobuf.json_format import MessageToDict

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

from claude_agent_sdk import tool, create_sdk_mcp_server


def create_vm_mcp_server(vm: MiniRuntimeClientSync, vm_state: dict):
    """Create an MCP server that wraps BitGN VM tools for use with ClaudeSDKClient."""

    @tool("tree", "Get directory tree / filesystem outline for a path", {"path": str})
    async def tree_tool(args):
        path = args.get("path", "/")
        try:
            result = vm.outline(OutlineRequest(path=path))
            text = json.dumps(MessageToDict(result), indent=2)
        except ConnectError as e:
            text = f"ERROR {e.code}: {e.message}"
        return {"content": [{"type": "text", "text": text}]}

    @tool(
        "search",
        "Search for a regex pattern in files under a path",
        {"pattern": str, "path": str, "count": int},
    )
    async def search_tool(args):
        try:
            result = vm.search(
                SearchRequest(
                    path=args.get("path", "/"),
                    pattern=args["pattern"],
                    count=args.get("count", 5),
                )
            )
            text = json.dumps(MessageToDict(result), indent=2)
        except ConnectError as e:
            text = f"ERROR {e.code}: {e.message}"
        return {"content": [{"type": "text", "text": text}]}

    @tool("list", "List directory contents", {"path": str})
    async def list_tool(args):
        try:
            result = vm.list(ListRequest(path=args["path"]))
            text = json.dumps(MessageToDict(result), indent=2)
        except ConnectError as e:
            text = f"ERROR {e.code}: {e.message}"
        return {"content": [{"type": "text", "text": text}]}

    @tool("read", "Read the contents of a file", {"path": str})
    async def read_tool(args):
        try:
            result = vm.read(ReadRequest(path=args["path"]))
            text = result.content
        except ConnectError as e:
            text = f"ERROR {e.code}: {e.message}"
        return {"content": [{"type": "text", "text": text}]}

    @tool("write", "Write content to a file", {"path": str, "content": str})
    async def write_tool(args):
        try:
            result = vm.write(WriteRequest(path=args["path"], content=args["content"]))
            text = json.dumps(MessageToDict(result), indent=2)
        except ConnectError as e:
            text = f"ERROR {e.code}: {e.message}"
        return {"content": [{"type": "text", "text": text}]}

    @tool("delete", "Delete a file", {"path": str})
    async def delete_tool(args):
        try:
            result = vm.delete(DeleteRequest(path=args["path"]))
            text = json.dumps(MessageToDict(result), indent=2)
        except ConnectError as e:
            text = f"ERROR {e.code}: {e.message}"
        return {"content": [{"type": "text", "text": text}]}

    @tool(
        "report_completion",
        "Report task completion with the final answer. Call this when the task is done.",
        {
            "answer": str,
            "completed_steps_laconic": list,
            "grounding_refs": list,
            "code": str,
        },
    )
    async def report_completion_tool(args):
        answer = (args.get("answer") or "").strip()
        if not answer:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": "ERROR: answer is empty. Re-read relevant files and provide a non-empty answer.",
                    }
                ]
            }
        grounding_refs = args.get("grounding_refs") or []
        if isinstance(grounding_refs, str):
            try:
                grounding_refs = json.loads(grounding_refs)
            except Exception:
                grounding_refs = []
        try:
            vm.answer(AnswerRequest(answer=answer, refs=grounding_refs))
        except ConnectError as e:
            return {"content": [{"type": "text", "text": f"ERROR submitting answer {e.code}: {e.message}"}]}
        vm_state["final_answer"] = answer
        vm_state["final_code"] = args.get("code", "completed")
        vm_state["grounding_refs"] = grounding_refs
        return {"content": [{"type": "text", "text": f"Task submitted successfully. Answer: {answer}"}]}

    return create_sdk_mcp_server(
        "vm-tools",
        tools=[tree_tool, search_tool, list_tool, read_tool, write_tool, delete_tool, report_completion_tool],
    )
