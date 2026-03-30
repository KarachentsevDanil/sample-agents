"""ReAct execution loop — raw OpenAI API with tool calls."""

import json
import time

from openai import OpenAI

from ..infra._cli import CLI_RED, CLI_GREEN, CLI_CLR
from ..infra.reasoning import supports_reasoning
from ..models import AgentRuntimeContext, PipelineContext
from ..prompt_resources.prompt_manager import PromptManager
from ..prompt_resources.prompts import build_initial_user_message

from .react_tools import TOOL_SCHEMAS, dispatch_tool


class ReActLoopStage:
    """ReAct execution loop using raw OpenAI chat completions with tool calls."""

    def __init__(self, vm, model: str, reasoning: str | None,
                 prompt_manager: PromptManager, client: OpenAI):
        self._vm = vm
        self._model = model
        self._reasoning = reasoning
        self._prompt_manager = prompt_manager
        self._client = client

    def execute(self, ctx: PipelineContext, logger) -> None:
        prompt = build_initial_user_message(ctx)
        max_steps = ctx.react_max_steps
        runtime = AgentRuntimeContext(vm=self._vm, pipeline=ctx, logger=logger, model=self._model)

        messages = [
            {"role": "system", "content": self._prompt_manager.get("system")},
            {"role": "user", "content": prompt},
        ]

        usage_totals = {"requests": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

        try:
            for _turn in range(max_steps):
                api_kwargs = dict(
                    model=self._model,
                    messages=messages,
                    tools=TOOL_SCHEMAS,
                )
                if self._reasoning and supports_reasoning(self._model):
                    api_kwargs["reasoning_effort"] = self._reasoning
                response = self._client.chat.completions.create(**api_kwargs)
                _accumulate_usage(usage_totals, response)

                choice = response.choices[0]
                assistant_msg = choice.message

                # Append assistant message to conversation history
                messages.append(_serialize_message(assistant_msg))

                # Log reasoning text if present
                if assistant_msg.content and assistant_msg.content.strip():
                    logger.append_api_call({
                        "stage": "react_reasoning",
                        "ts": time.time(),
                        "text": assistant_msg.content,
                        "type": "reasoning",
                    })

                # No tool calls — model stopped on its own
                if not assistant_msg.tool_calls:
                    break

                # Dispatch each tool call
                done_called = False
                for tc in assistant_msg.tool_calls:
                    arguments = json.loads(tc.function.arguments)
                    result = dispatch_tool(tc.function.name, arguments, runtime)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result,
                    })
                    if tc.function.name == "done":
                        done_called = True

                if done_called:
                    break

        except Exception as exc:
            print(f"{CLI_RED}ReAct stage failed: {exc}{CLI_CLR}")
            if not ctx.loop_termination_reason:
                ctx.loop_termination_reason = "exception"
            return

        logger.append_api_call({
            "stage": "react_run",
            "ts": time.time(),
            "model": self._model,
            "usage": usage_totals if usage_totals["requests"] > 0 else None,
            "turns": usage_totals["requests"],
        })

        # Determine termination reason
        if ctx.harness_answer_submitted:
            if not ctx.loop_termination_reason:
                ctx.loop_termination_reason = "report_completion"
        elif ctx.final_answer and ctx.final_code:
            if not ctx.loop_termination_reason:
                ctx.loop_termination_reason = "report_completion"
        else:
            if not ctx.loop_termination_reason:
                ctx.loop_termination_reason = "max_steps_reached"


def _serialize_message(msg) -> dict:
    """Convert a ChatCompletionMessage to a dict suitable for the messages list."""
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
