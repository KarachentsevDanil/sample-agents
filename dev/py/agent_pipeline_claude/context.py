import time

import anthropic
from connectrpc.errors import ConnectError

from bitgn.vm.pcm_connect import PcmRuntimeClientSync
from bitgn.vm.pcm_pb2 import ReadRequest, TreeRequest
from google.protobuf.json_format import MessageToDict

from .models import FileSuggestion, PipelineContext
from ._logging import build_api_log_entry
from .prompt_manager import PromptManager

MAX_PREREAD_FILES = 8


class ContextBuilderStage:
    def __init__(self, vm: PcmRuntimeClientSync, client: anthropic.Anthropic, model: str,
                 prompt_manager: PromptManager):
        self._vm = vm
        self._client = client
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

    def _suggest_files(self, ctx: PipelineContext, logger) -> list:
        if not ctx.dfs_tree:
            return []
        try:
            user_content = (
                f"Task: {ctx.task}\n\n"
                f"AGENTS.md:\n{ctx.agents_md}\n\n"
                f"Filesystem:\n{ctx.dfs_tree}"
            )
            resp = self._client.messages.parse(
                model=self._model,
                max_tokens=1024,
                system=self._prompt_manager.get("context"),
                messages=[{"role": "user", "content": user_content}],
                output_format=FileSuggestion,
            )
            logger.append_api_call(build_api_log_entry(
                "context", self._model, self._prompt_manager.get("context"),
                [{"role": "user", "content": user_content}], resp,
            ))
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
