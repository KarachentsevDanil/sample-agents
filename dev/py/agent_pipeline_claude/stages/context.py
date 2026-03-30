"""Context stage — fetches fundamentals and runs LLM-driven discovery.

Produces: agents_md, dfs_tree, vm_time, preloaded_context_files.
"""

from __future__ import annotations

import json
import time

import anthropic
from connectrpc.errors import ConnectError
from google.protobuf.json_format import MessageToDict

from bitgn.vm.pcm_pb2 import ContextRequest, ReadRequest, TreeRequest

from ..models import PipelineContext
from ..prompt_resources.prompt_manager import PromptManager
from ..infra.usage import summarize_anthropic_usage


def _normalize_repo_path(path: str) -> str:
    parts: list[str] = []
    for part in path.replace("\\", "/").split("/"):
        if not part or part == ".":
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    result = "/".join(parts)
    return "" if result == "." else result


class ContextBuilderStage:
    def __init__(self, vm, client: anthropic.Anthropic, model: str,
                 assess_model: str, prompt_manager: PromptManager):
        self._vm = vm
        self._client = client
        self._model = model
        self._assess_model = assess_model
        self._prompt_manager = prompt_manager

    def execute(self, ctx: PipelineContext, logger) -> None:
        # 1. Fetch fundamentals
        ctx.agents_md_path, ctx.agents_md = self._fetch_agents_md()
        ctx.dfs_tree = self._fetch_dfs()
        ctx.past_mistakes = logger.load_past_mistakes()
        ctx.vm_time = self._fetch_vm_time()

        if not ctx.dfs_tree:
            print("[CONTEXT_BUILDER] Skipped: no filesystem available")
            return

        # 2. LLM-driven context discovery
        from .context_agent import ContextDiscoveryAgent

        discovery = ContextDiscoveryAgent(self._vm, self._client, self._model, self._prompt_manager)
        discovered = discovery.execute(
            agents_md=ctx.agents_md,
            agents_md_path=ctx.agents_md_path,
            dfs_tree=ctx.dfs_tree,
            task=ctx.task,
            logger=logger,
        )

        # 3. Store discovered files (minus root AGENTS.md) as preloaded context
        agents_norm = _normalize_repo_path(ctx.agents_md_path).lower()
        ctx.preloaded_context_files = {
            path: content for path, content in discovered.items()
            if _normalize_repo_path(path).lower() != agents_norm
        }

        # 4. Run context assessment to extract injection_risk_notes
        ctx.injection_risk_notes = self._assess_context(ctx, logger)

    def _assess_context(self, ctx: PipelineContext, logger) -> str:
        """Run context assessment to extract injection_risk_notes."""
        try:
            prompt = self._prompt_manager.get("context")
        except (KeyError, FileNotFoundError):
            return ""

        parts = [f"Task: {ctx.task}"]
        if ctx.agents_md:
            parts.append(f"Trusted rule graph root ({ctx.agents_md_path}):\n{ctx.agents_md}")
        for path, content in ctx.preloaded_context_files.items():
            parts.append(f"--- {path} ---\n{content[:800]}")
        if ctx.dfs_tree:
            parts.append(f"Filesystem:\n{ctx.dfs_tree}")
        user_msg = "\n\n".join(parts)

        try:
            t0 = time.time()
            resp = self._client.messages.create(
                model=self._assess_model,
                max_tokens=1024,
                system=prompt + "\n\nRespond with a JSON object only.",
                messages=[{"role": "user", "content": user_msg}],
            )
            logger.append_api_call({
                "stage": "context_assessment",
                "ts": time.time(),
                "model": self._assess_model,
                "elapsed_ms": int((time.time() - t0) * 1000),
                "usage": summarize_anthropic_usage(resp),
            })
            text = resp.content[0].text if resp.content else ""
            # Extract JSON from response
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                parsed = json.loads(text[start:end])
                notes = parsed.get("injection_risk_notes", "")
                if notes:
                    print(f"[CONTEXT_ASSESS] Injection risk notes: {str(notes)[:120]}")
                return notes if isinstance(notes, str) else str(notes)
            return ""
        except Exception as e:
            print(f"[CONTEXT_ASSESS] Failed ({e}), continuing without assessment")
            return ""

    def _fetch_agents_md(self) -> tuple[str, str]:
        for path in ("/AGENTS.MD", "/AGENTS.md"):
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
        except (AttributeError, ConnectError):
            return ""

        # TreeResponse is a recursive tree — flatten to file paths
        paths: list[str] = []
        root_entry = data.get("root")
        if not root_entry:
            return ""

        stack = [(root_entry, "")]
        while stack:
            entry, parent = stack.pop()
            name = entry.get("name", "")
            if name in ("", "/"):
                current = parent
            else:
                current = f"{parent}/{name}" if parent else name

            if not entry.get("isDir", False) and current:
                paths.append(current)

            for child in reversed(entry.get("children") or []):
                stack.append((child, current))

        return "\n".join(paths)

    def _fetch_vm_time(self) -> str:
        try:
            resp = self._vm.context(ContextRequest())
            data = MessageToDict(resp)
            return data.get("time", "")
        except (ConnectError, Exception):
            return ""
