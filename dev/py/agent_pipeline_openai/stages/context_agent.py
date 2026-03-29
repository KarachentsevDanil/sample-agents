"""LLM-driven context discovery — replaces deterministic BFS.

Uses the same nano model with a read_file tool to follow the authority
chain from AGENTS.md through all referenced files. A HashSet (discovered)
prevents re-reads. The agent reads files, sees references inside them,
reads those, and continues until no new refs remain.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, List

from connectrpc.errors import ConnectError

from bitgn.vm.pcm_pb2 import ReadRequest

from ..infra._cli import CLI_GREEN, CLI_CLR
from ..infra.usage import summarize_result_usage
from ..prompt_resources.prompt_manager import PromptManager

MAX_CONTEXT_READS = 12

try:
    from agents import Agent, RunContextWrapper, Runner, function_tool
    from agents.agent import ToolsToFinalOutputResult
except ImportError:
    Agent = None
    RunContextWrapper = Any
    Runner = None
    function_tool = None
    ToolsToFinalOutputResult = None


# ── Agent context ─────────────────────────────────────────────────

@dataclass
class DiscoveryContext:
    vm: Any
    discovered: dict[str, str] = field(default_factory=dict)
    read_order: list[str] = field(default_factory=list)


# ── Tools ─────────────────────────────────────────────────────────

if function_tool is not None:

    @function_tool
    def read_file(wrapper: RunContextWrapper[DiscoveryContext], path: str) -> str:
        """Read a file from the repo. Returns content or NOT_FOUND.

        Args:
            path: File path to read (e.g., 90_memory/Soul.md)
        """
        ctx = wrapper.context
        norm = path.lstrip("/")
        if any(norm.lower() == k.lower() for k in ctx.discovered):
            return f"[ALREADY READ] {path} — skip, look for other references."
        if len(ctx.discovered) >= MAX_CONTEXT_READS:
            return "Read budget exhausted. Call done() now."
        try:
            content = ctx.vm.read(ReadRequest(path=path)).content
            ctx.discovered[norm] = content
            ctx.read_order.append(norm)
            print(f"  {CLI_GREEN}[CONTEXT_AGENT]{CLI_CLR} read: {path} ({len(content)} chars)")
            return content
        except ConnectError:
            return f"NOT_FOUND: {path}"

    @function_tool
    def done(wrapper: RunContextWrapper[DiscoveryContext]) -> str:
        """Signal that all authority files have been discovered.

        Call this when you have read all referenced rule/authority files.
        """
        count = len(wrapper.context.discovered)
        print(f"  {CLI_GREEN}[CONTEXT_AGENT]{CLI_CLR} done — {count} files discovered")
        return "DONE"


def _stop_on_done(wrapper: Any, results: list) -> Any:
    for result in results:
        tool = getattr(result, "tool", None)
        tool_name = getattr(tool, "name", "")
        if tool_name == "done":
            return ToolsToFinalOutputResult(
                is_final_output=True,
                final_output=getattr(result, "output", ""),
            )
    return ToolsToFinalOutputResult(is_final_output=False)


# ── Agent stage ───────────────────────────────────────────────────

class ContextDiscoveryAgent:
    """Discovers authority files by following refs from AGENTS.md."""

    def __init__(self, vm, model: str, prompt_manager: PromptManager):
        self._vm = vm
        self._model = model
        self._prompt_manager = prompt_manager

    def execute(
        self,
        agents_md: str,
        agents_md_path: str,
        dfs_tree: str,
        task: str,
        logger,
    ) -> dict[str, str]:
        """Run the discovery agent. Returns {path: content} of authority files."""
        if Agent is None or Runner is None or function_tool is None:
            print("[CONTEXT_AGENT] Skipped: openai-agents not installed")
            if agents_md_path and agents_md:
                return {agents_md_path: agents_md}
            return {}

        # Seed with root AGENTS.md if available
        disc_ctx = DiscoveryContext(vm=self._vm)
        if agents_md_path and agents_md:
            disc_ctx.discovered[agents_md_path.lstrip("/")] = agents_md

        parts = [f"Task (for context — do NOT execute): {task}"]
        if agents_md_path and agents_md:
            parts.append(f"Root AGENTS.md ({agents_md_path}):\n{agents_md}")
        else:
            parts.append("No root AGENTS.md found. Look for nested AGENTS.md in the tree.")
        parts.append(f"Filesystem tree:\n{dfs_tree}")
        user_input = "\n\n".join(parts)

        agent = Agent(
            name="Context Discovery",
            instructions=self._prompt_manager.get("context_agent"),
            model=self._model,
            tools=[read_file, done],
            tool_use_behavior=_stop_on_done,
        )

        print(f"[CONTEXT_AGENT] Starting discovery from {agents_md_path}")
        t0 = time.time()

        try:
            result = Runner.run_sync(
                agent, input=user_input, context=disc_ctx, max_turns=MAX_CONTEXT_READS,
            )
            elapsed_ms = int((time.time() - t0) * 1000)
            usage = summarize_result_usage(result)

            logger.append_api_call({
                "stage": "context_agent",
                "ts": time.time(),
                "model": self._model,
                "files_discovered": list(disc_ctx.discovered.keys()),
                "read_count": len(disc_ctx.discovered),
                "read_order": disc_ctx.read_order,
                "elapsed_ms": elapsed_ms,
                "usage": usage,
            })
            print(
                f"[CONTEXT_AGENT] Done: {len(disc_ctx.discovered)} files, "
                f"{elapsed_ms}ms, {usage.get('total_tokens', 0)} tokens"
            )

        except Exception as e:
            print(f"[CONTEXT_AGENT] Failed ({e}), returning partial: {list(disc_ctx.discovered.keys())}")

        return disc_ctx.discovered
