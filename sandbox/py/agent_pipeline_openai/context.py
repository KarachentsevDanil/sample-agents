import json

from connectrpc.errors import ConnectError
from google.protobuf.json_format import MessageToDict
from bitgn.vm.mini_pb2 import OutlineRequest, ReadRequest

from .models import FileSuggestion, PipelineContext
from .prompts import CONTEXT_SUGGESTION_PROMPT

MAX_PREREAD_FILES = 8

try:
    from agents import Agent, ModelSettings, Runner
except ImportError:
    Agent = None
    ModelSettings = None
    Runner = None


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
        if not ctx.dfs_tree or Agent is None or Runner is None:
            return []

        agent = Agent(
            name="Context Builder",
            instructions=CONTEXT_SUGGESTION_PROMPT,
            model=self._model,
            output_type=FileSuggestion,
            model_settings=ModelSettings() if ModelSettings is not None else None,
        )
        prompt = (
            f"Task: {ctx.task}\n\n"
            f"AGENTS.md:\n{ctx.agents_md}\n\n"
            f"Filesystem:\n{ctx.dfs_tree}"
        )
        try:
            result = Runner.run_sync(agent, input=prompt)
        except Exception:
            return []

        parsed = result.final_output
        if isinstance(parsed, FileSuggestion):
            return (parsed.files_to_read or [])[:MAX_PREREAD_FILES]
        return []

    def _read_files(self, paths: list[str]) -> dict:
        out = {}
        for path in paths:
            try:
                out[path] = self._vm.read(ReadRequest(path=path)).content
            except ConnectError:
                continue
        return out
