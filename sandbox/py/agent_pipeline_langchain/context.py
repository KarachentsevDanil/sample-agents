import json

from connectrpc.errors import ConnectError
from google.protobuf.json_format import MessageToDict

from bitgn.vm.mini_pb2 import OutlineRequest, ReadRequest

from agent_pipeline.models import FileSuggestion, PipelineContext
from agent_pipeline.prompts import CONTEXT_SUGGESTION_PROMPT
from openai_config import OPENAI_API_KEY

MAX_PREREAD_FILES = 8

try:
    from langchain_core.messages import HumanMessage, SystemMessage
    from langchain_openai import ChatOpenAI
except ImportError:
    ChatOpenAI = None
    HumanMessage = None
    SystemMessage = None


class ContextBuilderStage:
    def __init__(self, vm, model: str):
        self._vm = vm
        self._model = model

    def execute(self, ctx: PipelineContext, logger) -> None:
        ctx.agents_md_path, ctx.agents_md = self._fetch_agents_md()
        ctx.dfs_tree = self._fetch_dfs()
        suggested = self._suggest_files(ctx)
        ctx.preread_files = self._read_files(suggested)
        ctx.past_mistakes = logger.load_past_mistakes()

    def _fetch_agents_md(self) -> tuple[str, str]:
        for path in ("AGENTS.md", "AGENTS.MD"):
            try:
                content = self._vm.read(ReadRequest(path=path)).content
                return path, content
            except ConnectError:
                continue
        return "", ""

    def _fetch_dfs(self) -> str:
        try:
            resp = self._vm.outline(OutlineRequest(path="/"))
            return json.dumps(MessageToDict(resp), indent=2)
        except ConnectError:
            return ""

    def _suggest_files(self, ctx: PipelineContext) -> list[str]:
        if not ctx.dfs_tree or ChatOpenAI is None:
            return []

        llm = ChatOpenAI(
            model=self._model,
            api_key=OPENAI_API_KEY,
        ).with_structured_output(FileSuggestion, method="function_calling")
        try:
            result = llm.invoke([
                SystemMessage(content=CONTEXT_SUGGESTION_PROMPT),
                HumanMessage(content=(
                    f"Task: {ctx.task}\n\n"
                    f"AGENTS.md:\n{ctx.agents_md}\n\n"
                    f"Filesystem:\n{ctx.dfs_tree}"
                )),
            ])
        except Exception:
            return []

        if isinstance(result, FileSuggestion):
            return (result.files_to_read or [])[:MAX_PREREAD_FILES]
        if isinstance(result, dict):
            return (result.get("files_to_read") or [])[:MAX_PREREAD_FILES]
        return []

    def _read_files(self, paths: list[str]) -> dict:
        out = {}
        for path in paths:
            try:
                out[path] = self._vm.read(ReadRequest(path=path)).content
            except ConnectError:
                continue
        return out
