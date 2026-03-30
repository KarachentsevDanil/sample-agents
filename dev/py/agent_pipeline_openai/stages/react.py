import time
from typing import Any, List

from ..infra._cli import CLI_RED, CLI_GREEN, CLI_CLR
from ..models import AgentRuntimeContext, PipelineContext
from ..prompt_resources.prompt_manager import PromptManager
from ..prompt_resources.prompts import build_initial_user_message
from ..infra.usage import summarize_result_usage

from .react_tools import (
    OUTCOME_BY_NAME,
    read_file, browse, search, write_file, delete_file, move_file, done,
)

try:
    from agents import Agent, Runner
    from agents.agent import ToolsToFinalOutputResult
    from agents.items import ItemHelpers, MessageOutputItem
except ImportError as exc:
    Agent = None
    Runner = None
    ToolsToFinalOutputResult = None
    ItemHelpers = None
    MessageOutputItem = None
    _IMPORT_ERROR = exc
else:
    _IMPORT_ERROR = None


def _ensure_sdk() -> None:
    if Agent is None or Runner is None or ToolsToFinalOutputResult is None:
        raise RuntimeError(
            "OpenAI Agents SDK backend selected, but `openai-agents` is not installed. "
            "Install it with `uv add openai-agents`."
        ) from _IMPORT_ERROR


def _stop_on_done(wrapper: Any, results: List[Any]) -> Any:
    for result in results:
        tool = getattr(result, "tool", None)
        tool_name = getattr(tool, "name", "")
        if tool_name == "done":
            return ToolsToFinalOutputResult(
                is_final_output=True,
                final_output=getattr(result, "output", ""),
            )
    return ToolsToFinalOutputResult(is_final_output=False)


class ReActLoopStage:
    """ReAct execution loop using the OpenAI Agents SDK — v2 with 7 tools."""

    def __init__(self, vm, model: str, prompt_manager: PromptManager):
        self._vm = vm
        self._model = model
        self._prompt_manager = prompt_manager

    def execute(self, ctx: PipelineContext, logger) -> None:
        _ensure_sdk()
        prompt = build_initial_user_message(ctx)

        max_steps = ctx.react_max_steps
        runtime = AgentRuntimeContext(vm=self._vm, pipeline=ctx, logger=logger, model=self._model)
        agent = Agent(
            name="Dev ReAct Agent",
            instructions=self._prompt_manager.get("system"),
            model=self._model,
            tools=[read_file, browse, search, write_file, delete_file, move_file, done],
            tool_use_behavior=_stop_on_done,
        )
        try:
            result = Runner.run_sync(agent, input=prompt, context=runtime, max_turns=max_steps)
        except Exception as exc:
            print(f"{CLI_RED}ReAct stage failed: {exc}{CLI_CLR}")
            if not ctx.loop_termination_reason:
                ctx.loop_termination_reason = "exception"
            return

        logger.append_api_call({
            "stage": "react_run",
            "ts": time.time(),
            "model": self._model,
            "usage": summarize_result_usage(result),
            "turns": len(getattr(result, "raw_responses", []) or []),
        })

        # Extract and log assistant reasoning
        if ItemHelpers is not None and MessageOutputItem is not None:
            for item in getattr(result, 'new_items', []):
                if isinstance(item, MessageOutputItem):
                    text = ItemHelpers.text_message_output(item)
                    if text.strip():
                        logger.append_api_call({
                            "stage": "react_reasoning",
                            "ts": time.time(),
                            "text": text,
                            "type": "reasoning",
                        })

        # Check if done() was called (harness_answer_submitted flag)
        if ctx.harness_answer_submitted:
            if not ctx.loop_termination_reason:
                ctx.loop_termination_reason = "report_completion"
        elif ctx.final_answer and ctx.final_code:
            if not ctx.loop_termination_reason:
                ctx.loop_termination_reason = "report_completion"
        else:
            if not ctx.loop_termination_reason:
                ctx.loop_termination_reason = "max_steps_reached"
