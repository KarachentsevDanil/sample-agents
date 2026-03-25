import time

from connectrpc.errors import ConnectError
from google.protobuf.json_format import MessageToDict
from bitgn.vm.pcm_pb2 import ReadRequest, TreeRequest

from .models import FileSuggestion, PipelineContext
from .prompt_manager import PromptManager

MAX_PREREAD_FILES = 8

try:
    from agents import Agent, Runner
except ImportError:
    Agent = None
    Runner = None


class ContextBuilderStage:
    def __init__(self, vm, model: str, prompt_manager: PromptManager):
        self._vm = vm
        self._model = model
        self._prompt_manager = prompt_manager

    # Files that should always be pre-read if they exist (saves ReAct loop steps)
    MANDATORY_PREREAD_PATTERNS = [
        "90_memory/Soul.md",
        "02_distill/AGENTS.md",
        "99_process/process_tasks.md",
        "99_process/document_capture.md",
        "02_distill/cards/_card-template.md",
        "02_distill/threads/_thread-template.md",
    ]

    def execute(self, ctx: PipelineContext, logger) -> None:
        ctx.agents_md_path, ctx.agents_md = self._fetch_agents_md()
        ctx.dfs_tree = self._fetch_dfs()
        mandatory = self._mandatory_preread(ctx.dfs_tree)
        suggested = self._suggest_files(ctx, logger)
        # Merge mandatory + suggested, dedup, respect limit
        all_paths = list(dict.fromkeys(mandatory + suggested))[:MAX_PREREAD_FILES]
        ctx.preread_files = self._read_files(all_paths)
        ctx.past_mistakes = logger.load_past_mistakes()

    def _mandatory_preread(self, dfs_tree: str) -> list[str]:
        """Return mandatory files that exist in the filesystem tree."""
        tree_lines = set(dfs_tree.splitlines())
        return [p for p in self.MANDATORY_PREREAD_PATTERNS if p in tree_lines]

    def _fetch_agents_md(self) -> tuple[str, str]:
        for path in ("AGENTS.MD", "AGENTS.md"):
            try:
                content = self._vm.read(ReadRequest(path=path)).content
                return path, content
            except ConnectError:
                continue
        return "", ""

    def _fetch_dfs(self) -> str:
        try:
            resp = self._vm.tree(TreeRequest(root=""))
            data = MessageToDict(resp)
            return "\n".join(e["path"] for e in (data.get("entries") or []))
        except ConnectError:
            return ""

    def _suggest_files(self, ctx: PipelineContext, logger) -> list[str]:
        if not ctx.dfs_tree or Agent is None or Runner is None:
            return []

        agent = Agent(
            name="Context Builder",
            instructions=self._prompt_manager.get("context"),
            model=self._model,
            output_type=FileSuggestion,
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

        logger.append_api_call({
            "stage": "context",
            "ts": time.time(),
            "model": self._model,
            "input_fragment": prompt[:200],
        })
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
