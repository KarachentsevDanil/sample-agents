"""LLM-driven context discovery — follows AGENTS.md authority chain.

Uses the Anthropic tool_use API in a loop to read all authority files
referenced from AGENTS.md and its transitive references.
"""

from __future__ import annotations

import time

import anthropic
from connectrpc.errors import ConnectError

from bitgn.vm.pcm_pb2 import ReadRequest

from ..infra._cli import CLI_GREEN, CLI_CLR
from ..infra.usage import summarize_anthropic_usage
from ..prompt_resources.prompt_manager import PromptManager

MAX_CONTEXT_READS = 12

CONTEXT_DISCOVERY_TOOLS = [
    {
        "name": "read_file",
        "description": "Read a file from the repo. Returns content or NOT_FOUND.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "File path to read"}},
            "required": ["path"],
        },
    },
    {
        "name": "done",
        "description": "Signal that all authority files have been discovered. Call when finished.",
        "input_schema": {"type": "object", "properties": {}},
    },
]


class ContextDiscoveryAgent:
    """Discovers authority files by following refs from AGENTS.md."""

    def __init__(self, vm, client: anthropic.Anthropic, model: str, prompt_manager: PromptManager):
        self._vm = vm
        self._client = client
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
        discovered: dict[str, str] = {}

        # Seed with root AGENTS.md if available
        if agents_md_path and agents_md:
            discovered[agents_md_path.lstrip("/")] = agents_md

        parts = [f"Task (for context — do NOT execute): {task}"]
        if agents_md_path and agents_md:
            parts.append(f"Root AGENTS.md ({agents_md_path}):\n{agents_md}")
        else:
            parts.append("No root AGENTS.md found. Look for nested AGENTS.md in the tree.")
        parts.append(f"Filesystem tree:\n{dfs_tree}")
        user_input = "\n\n".join(parts)

        messages = [{"role": "user", "content": user_input}]
        reads = 0
        print(f"[CONTEXT_AGENT] Starting discovery from {agents_md_path}")
        t0 = time.time()

        try:
            while reads < MAX_CONTEXT_READS:
                response = self._client.messages.create(
                    model=self._model,
                    max_tokens=2048,
                    system=self._prompt_manager.get("context_agent"),
                    tools=CONTEXT_DISCOVERY_TOOLS,
                    messages=messages,
                )
                messages.append({"role": "assistant", "content": response.content})

                if response.stop_reason != "tool_use":
                    break

                tool_results = []
                should_stop = False

                for block in response.content:
                    if block.type != "tool_use":
                        continue

                    if block.name == "done":
                        print(f"  {CLI_GREEN}[CONTEXT_AGENT]{CLI_CLR} done — {len(discovered)} files discovered")
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": "DONE",
                        })
                        should_stop = True
                        continue

                    if block.name == "read_file":
                        path = block.input.get("path", "")
                        norm = path.lstrip("/")

                        if any(norm.lower() == k.lower() for k in discovered):
                            result = f"[ALREADY READ] {path} — skip, look for other references."
                        elif len(discovered) >= MAX_CONTEXT_READS:
                            result = "Read budget exhausted. Call done() now."
                        else:
                            try:
                                content = self._vm.read(ReadRequest(path=path)).content
                                discovered[norm] = content
                                reads += 1
                                print(f"  {CLI_GREEN}[CONTEXT_AGENT]{CLI_CLR} read: {path} ({len(content)} chars)")
                                result = content
                            except ConnectError:
                                result = f"NOT_FOUND: {path}"

                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                        })

                if tool_results:
                    messages.append({"role": "user", "content": tool_results})

                if should_stop:
                    break

        except Exception as e:
            print(f"[CONTEXT_AGENT] Failed ({e}), returning partial: {list(discovered.keys())}")

        elapsed_ms = int((time.time() - t0) * 1000)
        logger.append_api_call({
            "stage": "context_agent",
            "ts": time.time(),
            "model": self._model,
            "files_discovered": list(discovered.keys()),
            "read_count": len(discovered),
            "elapsed_ms": elapsed_ms,
        })
        print(
            f"[CONTEXT_AGENT] Done: {len(discovered)} files, "
            f"{elapsed_ms}ms"
        )

        return discovered
