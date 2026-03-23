import anthropic
from connectrpc.errors import ConnectError

from bitgn.vm.mini_connect import MiniRuntimeClientSync
from bitgn.vm.mini_pb2 import OutlineRequest, ReadRequest
from google.protobuf.json_format import MessageToDict
import json

from agent_pipeline.models import FileSuggestion, PipelineContext
from agent_pipeline.prompts import CONTEXT_SUGGESTION_PROMPT

MAX_PREREAD_FILES = 8


class ContextBuilderStage:
    def __init__(self, vm: MiniRuntimeClientSync, client: anthropic.Anthropic, model: str):
        self._vm = vm
        self._client = client
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

    def _suggest_files(self, ctx: PipelineContext) -> list:
        if not ctx.dfs_tree:
            return []
        try:
            resp = self._client.messages.parse(
                model=self._model,
                max_tokens=1024,
                system=CONTEXT_SUGGESTION_PROMPT,
                messages=[
                    {
                        "role": "user",
                        "content": (
                            f"Task: {ctx.task}\n\n"
                            f"AGENTS.md:\n{ctx.agents_md}\n\n"
                            f"Filesystem:\n{ctx.dfs_tree}"
                        ),
                    }
                ],
                output_format=FileSuggestion,
            )
            result = resp.parsed_output
            return (result.files_to_read or [])[:MAX_PREREAD_FILES]
        except Exception:
            return []

    def _read_files(self, paths: list) -> dict:
        out = {}
        for path in paths:
            try:
                out[path] = self._vm.read(ReadRequest(path=path)).content
            except ConnectError:
                continue
        return out
