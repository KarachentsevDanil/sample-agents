"""ReAct execution loop using the Anthropic API."""

import time

import anthropic

from ..infra._cli import CLI_RED, CLI_GREEN, CLI_CLR
from ..infra.usage import summarize_anthropic_usage
from ..models import AgentRuntimeContext, PipelineContext
from ..prompt_resources.prompt_manager import PromptManager
from ..prompt_resources.prompts import build_initial_user_message

from .react_tools import TOOLS, dispatch_tool, dispatch_report_completion

_COMPRESSION_THRESHOLD = 6


def _serialize_messages(messages: list) -> list:
    return [
        {**m, "content": [b.model_dump() if hasattr(b, "model_dump") else b for b in m["content"]]}
        if isinstance(m.get("content"), list) else m
        for m in messages
    ]


class ReActLoopStage:
    """ReAct execution loop using the Anthropic tool_use API."""

    def __init__(self, vm, client: anthropic.Anthropic, model: str, prompt_manager: PromptManager):
        self._vm = vm
        self._client = client
        self._model = model
        self._prompt_manager = prompt_manager

    def execute(self, ctx: PipelineContext, logger) -> None:
        prompt = build_initial_user_message(ctx)
        messages = [{"role": "user", "content": prompt}]
        max_steps = ctx.react_max_steps
        runtime = AgentRuntimeContext(vm=self._vm, pipeline=ctx, logger=logger, model=self._model)

        while runtime.step_idx < max_steps:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=4096,
                system=self._prompt_manager.get("system"),
                tools=TOOLS,
                messages=messages,
            )

            usage = summarize_anthropic_usage(response)
            logger.append_api_call({
                "stage": "react",
                "ts": time.time(),
                "model": self._model,
                "step": runtime.step_idx,
                "usage": usage,
                "response_stop_reason": getattr(response, "stop_reason", None),
            })
            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason != "tool_use":
                if not ctx.loop_termination_reason:
                    ctx.loop_termination_reason = "no_tool_use"
                break

            tool_results = []
            step_reasoning = ""

            for block in response.content:
                if getattr(block, 'type', None) == 'text':
                    step_reasoning = block.text
                    continue
                if block.type != "tool_use":
                    continue

                tool_name = block.name
                tool_input = block.input
                tool_use_id = block.id

                if tool_name == "report_completion":
                    result_text = dispatch_report_completion(runtime, tool_input)
                else:
                    result_text = dispatch_tool(runtime, tool_name, tool_input)

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": result_text,
                })

            # Log per-turn agent reasoning
            if step_reasoning.strip():
                logger.append_react_step({
                    "type": "reasoning",
                    "node_id": str(runtime.step_idx),
                    "text": step_reasoning,
                    "ts": time.time(),
                })

            if tool_results:
                messages.append({"role": "user", "content": tool_results})
                self._maybe_compress_reads(messages, ctx)

            if ctx.harness_answer_submitted:
                break

        if not ctx.final_answer:
            if not ctx.loop_termination_reason:
                ctx.loop_termination_reason = "max_steps_reached"
            print(f"{CLI_RED}No answer produced after {runtime.step_idx} steps{CLI_CLR}")

    def _maybe_compress_reads(self, messages: list, ctx: PipelineContext) -> None:
        """Compress consecutive read tool_results to prevent unbounded context growth."""
        # Gather tool_use ids for read calls from all assistant messages
        read_ids: set[str] = set()
        for msg in messages:
            if msg.get("role") != "assistant":
                continue
            content = msg.get("content", [])
            if not isinstance(content, list):
                continue
            for block in content:
                btype = getattr(block, "type", None) or (block.get("type") if isinstance(block, dict) else None)
                if btype != "tool_use":
                    continue
                bname = getattr(block, "name", None) or (block.get("name") if isinstance(block, dict) else None)
                bid = getattr(block, "id", None) or (block.get("id") if isinstance(block, dict) else None)
                if bname == "read" and bid:
                    read_ids.add(bid)

        # Walk messages in reverse to count consecutive trailing read-only user turns
        consecutive_read_msgs: list[int] = []
        for idx in range(len(messages) - 1, -1, -1):
            msg = messages[idx]
            if msg.get("role") != "user":
                break
            content = msg.get("content", [])
            if not isinstance(content, list) or not content:
                break
            all_reads = all(
                (entry.get("type") == "tool_result" and entry.get("tool_use_id") in read_ids)
                for entry in content
            )
            if not all_reads:
                break
            consecutive_read_msgs.append(idx)

        if len(consecutive_read_msgs) < _COMPRESSION_THRESHOLD:
            return

        n = len(consecutive_read_msgs)
        paths = ctx.files_used[-n:] if ctx.files_used else []
        path_list = ", ".join(paths) if paths else f"{n} files"
        summary = (
            f"[COMPRESSED: agent read {n} files: {path_list}. "
            "Contents were pre-loaded in context.]"
        )
        compressed_entry = {
            "type": "tool_result",
            "tool_use_id": messages[consecutive_read_msgs[-1]]["content"][0]["tool_use_id"],
            "content": summary,
        }

        indices_to_remove = sorted(consecutive_read_msgs)
        for idx in reversed(indices_to_remove):
            messages.pop(idx)
        insert_at = indices_to_remove[0]
        messages.insert(insert_at, {"role": "user", "content": [compressed_entry]})
