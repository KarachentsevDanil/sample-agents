"""LLM-driven context discovery — raw OpenAI API with tool calls.

Uses the same nano model with a read_file tool to follow the authority
chain from AGENTS.md through all referenced files. A HashSet (discovered)
prevents re-reads. The agent reads files, sees references inside them,
reads those, and continues until no new refs remain.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from connectrpc.errors import ConnectError
from openai import OpenAI

from bitgn.vm.pcm_pb2 import ReadRequest

from ..infra._cli import CLI_GREEN, CLI_CLR
from ..prompt_resources.prompt_manager import PromptManager

MAX_CONTEXT_READS = 12


# ── Agent context ─────────────────────────────────────────────────

@dataclass
class DiscoveryContext:
    vm: Any
    discovered: dict[str, str] = field(default_factory=dict)
    read_order: list[str] = field(default_factory=list)


# ── Tools (plain functions) ──────────────────────────────────────

def _read_file(ctx: DiscoveryContext, path: str) -> str:
    """Read a file from the repo. Returns content or NOT_FOUND."""
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


def _done(ctx: DiscoveryContext) -> str:
    """Signal that all authority files have been discovered."""
    count = len(ctx.discovered)
    print(f"  {CLI_GREEN}[CONTEXT_AGENT]{CLI_CLR} done — {count} files discovered")
    return "DONE"


# ── Tool schemas & dispatch ──────────────────────────────────────

_TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file from the repo. Returns content or NOT_FOUND.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path to read (e.g., 90_memory/Soul.md)",
                    },
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "done",
            "description": (
                "Signal that all authority files have been discovered. "
                "Call this when you have read all referenced rule/authority files."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
        },
    },
]


def _dispatch_tool(name: str, arguments: dict, ctx: DiscoveryContext) -> str:
    if name == "read_file":
        return _read_file(ctx, **arguments)
    if name == "done":
        return _done(ctx)
    return f"ERROR: Unknown tool '{name}'"


# ── Agent stage ───────────────────────────────────────────────────

class ContextDiscoveryAgent:
    """Discovers authority files by following refs from AGENTS.md."""

    def __init__(self, vm, model: str, reasoning: str | None,
                 prompt_manager: PromptManager, client: OpenAI):
        self._vm = vm
        self._model = model
        self._reasoning = reasoning
        self._prompt_manager = prompt_manager
        self._client = client

    def execute(
        self,
        agents_md: str,
        agents_md_path: str,
        dfs_tree: str,
        task: str,
        logger,
    ) -> dict[str, str]:
        """Run the discovery agent. Returns {path: content} of authority files."""
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

        messages = [
            {"role": "system", "content": self._prompt_manager.get("context_agent")},
            {"role": "user", "content": user_input},
        ]

        print(f"[CONTEXT_AGENT] Starting discovery from {agents_md_path}")
        t0 = time.time()

        usage_totals = {"requests": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

        try:
            for _turn in range(MAX_CONTEXT_READS):
                api_kwargs = dict(
                    model=self._model,
                    messages=messages,
                    tools=_TOOL_SCHEMAS,
                )
                if self._reasoning:
                    api_kwargs["reasoning_effort"] = self._reasoning
                response = self._client.chat.completions.create(**api_kwargs)
                _accumulate_usage(usage_totals, response)

                choice = response.choices[0]
                assistant_msg = choice.message

                # Append assistant message to history
                messages.append(_serialize_message(assistant_msg))

                # No tool calls — model stopped
                if not assistant_msg.tool_calls:
                    break

                # Dispatch tool calls
                done_called = False
                for tc in assistant_msg.tool_calls:
                    arguments = json.loads(tc.function.arguments)
                    result = _dispatch_tool(tc.function.name, arguments, disc_ctx)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result,
                    })
                    if tc.function.name == "done":
                        done_called = True

                if done_called:
                    break

            elapsed_ms = int((time.time() - t0) * 1000)
            logger.append_api_call({
                "stage": "context_agent",
                "ts": time.time(),
                "model": self._model,
                "files_discovered": list(disc_ctx.discovered.keys()),
                "read_count": len(disc_ctx.discovered),
                "read_order": disc_ctx.read_order,
                "elapsed_ms": elapsed_ms,
                "usage": usage_totals if usage_totals["requests"] > 0 else None,
            })
            print(
                f"[CONTEXT_AGENT] Done: {len(disc_ctx.discovered)} files, "
                f"{elapsed_ms}ms, {usage_totals.get('total_tokens', 0)} tokens"
            )

        except Exception as e:
            print(f"[CONTEXT_AGENT] Failed ({e}), returning partial: {list(disc_ctx.discovered.keys())}")

        return disc_ctx.discovered


def _serialize_message(msg) -> dict:
    """Convert a ChatCompletionMessage to a dict for the messages list."""
    d = {"role": msg.role, "content": msg.content}
    if msg.tool_calls:
        d["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                },
            }
            for tc in msg.tool_calls
        ]
    return d


def _accumulate_usage(totals: dict, response) -> None:
    """Add token counts from a single API response to running totals."""
    totals["requests"] += 1
    usage = response.usage
    if usage:
        totals["input_tokens"] += usage.prompt_tokens or 0
        totals["output_tokens"] += usage.completion_tokens or 0
        totals["total_tokens"] += usage.total_tokens or 0
