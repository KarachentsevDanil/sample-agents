import time
from dataclasses import dataclass
from typing import Any

from connectrpc.errors import ConnectError

from agent_pipeline.models import PipelineContext


@dataclass
class AgentRuntimeContext:
    vm: Any
    pipeline: PipelineContext
    logger: Any
    model: str
    step_idx: int = 0


def record_file_use(ctx: AgentRuntimeContext, path: str) -> None:
    if path not in ctx.pipeline.files_used:
        ctx.pipeline.files_used.append(path)


def record_tool_success(
    ctx: AgentRuntimeContext,
    command: str,
    args: dict,
    result_summary: str,
) -> str:
    ctx.step_idx += 1
    ctx.logger.append_api_call({
        "step": ctx.step_idx,
        "cmd": command,
        "args": args,
        "result": result_summary,
        "ts": time.time(),
    })
    step_record = {
        "step": ctx.step_idx,
        "current_state": args.get("reason", "") or f"Run {command}",
        "plan": [args.get("reason", "") or f"Run {command}"],
        "function": command,
        "result_summary": result_summary[:400],
        "ts": time.time(),
    }
    ctx.pipeline.react_trace.append(step_record)
    ctx.logger.append_react_step(step_record)
    return result_summary


def record_tool_error(
    ctx: AgentRuntimeContext,
    command: str,
    args: dict,
    err: ConnectError,
) -> str:
    ctx.step_idx += 1
    message = err.message or str(err)
    ctx.logger.append_api_call({
        "step": ctx.step_idx,
        "cmd": command,
        "args": args,
        "error": message,
        "code": str(err.code),
        "ts": time.time(),
    })
    step_record = {
        "step": ctx.step_idx,
        "current_state": args.get("reason", "") or f"Run {command}",
        "plan": [args.get("reason", "") or f"Run {command}"],
        "function": command,
        "result_summary": message[:400],
        "ts": time.time(),
    }
    ctx.pipeline.react_trace.append(step_record)
    ctx.logger.append_react_step(step_record)
    return message
