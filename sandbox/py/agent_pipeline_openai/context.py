import json

from connectrpc.errors import ConnectError
from google.protobuf.json_format import MessageToDict
from bitgn.vm.mini_pb2 import ListRequest, ReadRequest

from .models import FileSuggestion, PipelineContext
from .prompt_manager import PromptManager

MAX_PREREAD_FILES = 8

try:
    from agents import Agent, ModelSettings, Runner
except ImportError:
    Agent = None
    ModelSettings = None
    Runner = None


class ContextBuilderStage:
    def __init__(self, vm, model: str, prompt_manager: PromptManager):
        self._vm = vm
        self._model = model
        self._prompt_manager = prompt_manager

    def execute(self, ctx: PipelineContext, logger) -> None:
        ctx.agents_md_path, ctx.agents_md = self._fetch_agents_md()
        ctx.dfs_tree = self._fetch_dfs()
        suggested = self._suggest_files(ctx)
        ctx.preread_files = self._read_files(suggested)
        ctx.past_mistakes = logger.load_past_mistakes()

    def _fetch_agents_md(self) -> tuple[str, str]:
        for path in ("AGENTS.MD", "AGENTS.md"):
            try:
                content = self._vm.read(ReadRequest(path=path)).content
                return path, content
            except ConnectError:
                continue
        return "", ""

    def _fetch_dfs(self) -> str:
        paths: list[str] = []
        self._walk("/", paths, depth=0)
        return "\n".join(paths)

    def _walk(self, vm_path: str, result: list[str], depth: int) -> None:
        if depth > 8:
            return
        try:
            resp = self._vm.list(ListRequest(path=vm_path))
            data = MessageToDict(resp)
            prefix = vm_path.strip("/")

            for fname in (data.get("files") or []):
                name = (fname.get("path") or fname.get("name") or fname) if isinstance(fname, dict) else fname
                if name:
                    result.append(f"{prefix}/{name}" if prefix else name)

            for dname in (data.get("folders") or []):
                name = (dname.get("path") or dname.get("name") or dname) if isinstance(dname, dict) else dname
                if name:
                    display = f"{prefix}/{name}" if prefix else name
                    result.append(display)
                    self._walk(f"/{display}", result, depth + 1)
        except ConnectError:
            pass

    def _suggest_files(self, ctx: PipelineContext) -> list[str]:
        if not ctx.dfs_tree or Agent is None or Runner is None:
            return []

        agent = Agent(
            name="Context Builder",
            instructions=self._prompt_manager.get("context"),
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
        if isinstance(parsed, list):
            return [p for p in parsed if isinstance(p, str)][:MAX_PREREAD_FILES]
        if isinstance(parsed, dict):
            files = parsed.get("files_to_read") or []
            return [p for p in files if isinstance(p, str)][:MAX_PREREAD_FILES]
        if isinstance(parsed, str):
            try:
                decoded = json.loads(parsed)
            except Exception:
                return []
            if isinstance(decoded, list):
                return [p for p in decoded if isinstance(p, str)][:MAX_PREREAD_FILES]
            if isinstance(decoded, dict):
                files = decoded.get("files_to_read") or []
                return [p for p in files if isinstance(p, str)][:MAX_PREREAD_FILES]
        return []

    def _read_files(self, paths: list[str]) -> dict:
        out = {}
        for path in paths:
            try:
                out[path] = self._vm.read(ReadRequest(path=path)).content
            except ConnectError:
                continue
        return out
