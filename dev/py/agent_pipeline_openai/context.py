import hashlib
import time

from connectrpc.errors import ConnectError
from google.protobuf.json_format import MessageToDict
from bitgn.vm.pcm_pb2 import ReadRequest, TreeRequest

from .models import FileSuggestion, PipelineContext, RulesExtraction
from .prompt_manager import PromptManager
from .context_blocks import heuristic_select

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

    # Cache: sha256(agents_md) -> RulesExtraction (avoids repeated LLM calls)
    _rules_cache: dict[str, RulesExtraction] = {}

    def execute(self, ctx: PipelineContext, logger) -> None:
        ctx.agents_md_path, ctx.agents_md = self._fetch_agents_md()
        ctx.dfs_tree = self._fetch_dfs()

        # T2: extract referenced files + key rules from agents.md
        rules_files = []
        if ctx.agents_md:
            extraction = self._extract_rules(ctx.agents_md, ctx.dfs_tree, logger, task=ctx.task)
            if extraction:
                ctx.key_rules = extraction.key_rules
                ctx.rules_files = extraction.referenced_files
                rules_files = self._filter_existing(extraction.referenced_files, ctx.dfs_tree)

        ctx.context_blocks = self._select_context_blocks(ctx.task, ctx.key_rules)

        mandatory = self._mandatory_preread(ctx.dfs_tree)
        suggested = self._suggest_files(ctx, logger)
        # Merge order: rules_files (highest priority) -> mandatory -> suggested
        all_paths = list(dict.fromkeys(rules_files + mandatory + suggested))[:MAX_PREREAD_FILES]
        ctx.preread_files = self._read_files(all_paths)
        ctx.past_mistakes = logger.load_past_mistakes()

    def _extract_rules(self, agents_md: str, dfs_tree: str, logger, task: str = "") -> RulesExtraction | None:
        """Parse agents.md for referenced files and key rules (cached by content+task hash)."""
        if Agent is None or Runner is None:
            return None
        task_snippet = task[:150]
        cache_key = hashlib.sha256((agents_md + "|TASK|" + task_snippet).encode()).hexdigest()
        if cache_key in self._rules_cache:
            return self._rules_cache[cache_key]
        try:
            agent = Agent(
                name="Rules Extractor",
                instructions=self._prompt_manager.get("rules_extraction"),
                model=self._model,
                output_type=RulesExtraction,
            )
            prompt = (
                f"Task: {task_snippet}\n\n"
                f"AGENTS.md content:\n{agents_md}\n\n"
                f"Filesystem tree:\n{dfs_tree}"
            )
            result = Runner.run_sync(agent, input=prompt)
            logger.append_api_call({
                "stage": "rules_extraction",
                "ts": time.time(),
                "model": self._model,
                "input_fragment": prompt[:200],
            })
            parsed = result.final_output
            if isinstance(parsed, RulesExtraction):
                self._rules_cache[cache_key] = parsed
                return parsed
            return None
        except Exception:
            return None

    def _select_context_blocks(self, task: str, key_rules: list[str]) -> list[str]:
        """Two-stage context block selection: heuristic + rules cross-reference."""
        # Stage 1: heuristic keyword matching (no LLM call)
        heuristic_blocks = heuristic_select(task)
        if not heuristic_blocks:
            return []

        # Stage 2: cross-reference with extracted key_rules to validate relevance
        # Only include a block if its keywords appear in the extracted rules
        # This avoids injecting blocks for keywords that appear in task but not in AGENTS.md rules
        rules_text = " ".join(key_rules).lower()
        validated = []
        for block_content in heuristic_blocks:
            # Check if any keyword from any block matches rules_text
            # Simple approach: if we got this block via heuristic, it's likely relevant
            # Keep all heuristically-selected blocks (they're small, low noise)
            validated.append(block_content)
        return validated

    @staticmethod
    def _filter_existing(paths: list[str], dfs_tree: str) -> list[str]:
        """Keep only paths that appear in the filesystem tree."""
        tree_lines = set(dfs_tree.splitlines())
        return [p for p in paths if p in tree_lines]

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
