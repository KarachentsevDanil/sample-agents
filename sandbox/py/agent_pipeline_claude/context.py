import anthropic
from connectrpc.errors import ConnectError

from bitgn.vm.mini_connect import MiniRuntimeClientSync
from bitgn.vm.mini_pb2 import ListRequest, ReadRequest
from google.protobuf.json_format import MessageToDict

from agent_pipeline.models import FileSuggestion, PipelineContext
from .prompt_manager import PromptManager

MAX_PREREAD_FILES = 8


class ContextBuilderStage:
    def __init__(self, vm: MiniRuntimeClientSync, client: anthropic.Anthropic, model: str,
                 prompt_manager: PromptManager):
        self._vm = vm
        self._client = client
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

    def _walk(self, vm_path: str, result: list, depth: int) -> None:
        """Recursively enumerate all files and directories via list calls.

        vm_path: absolute path for VM API (with leading /).
        result: populated with display paths that have NO leading slash,
                matching the format expected by benchmark grounding_refs and answers.
        """
        if depth > 8:
            return
        try:
            resp = self._vm.list(ListRequest(path=vm_path))
            data = MessageToDict(resp)
            prefix = vm_path.strip("/")  # "/" → "", "/docs" → "docs"

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

    def _suggest_files(self, ctx: PipelineContext) -> list:
        if not ctx.dfs_tree:
            return []
        try:
            resp = self._client.messages.parse(
                model=self._model,
                max_tokens=1024,
                system=self._prompt_manager.get("context"),
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
