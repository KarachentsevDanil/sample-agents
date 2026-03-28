import json
import time

import anthropic
from connectrpc.errors import ConnectError
from google.protobuf.json_format import MessageToDict

from bitgn.vm.pcm_connect import PcmRuntimeClientSync
from bitgn.vm.pcm_pb2 import (
    AnswerRequest,
    ContextRequest,
    DeleteRequest,
    FindRequest,
    ListRequest,
    MkDirRequest,
    MoveRequest,
    Outcome,
    ReadRequest,
    SearchRequest,
    TreeRequest,
    WriteRequest,
)

from .models import PipelineContext
from .prompts import build_initial_user_message
from ._cli import CLI_RED, CLI_GREEN, CLI_CLR
from ._logging import build_api_log_entry
from .prompt_manager import PromptManager
from .tools import TOOLS
from .validator import ActionValidator

MAX_STEPS = 30

_COMPRESSION_THRESHOLD = 6

OUTCOME_BY_NAME = {
    "OUTCOME_OK": Outcome.OUTCOME_OK,
    "OUTCOME_DENIED_SECURITY": Outcome.OUTCOME_DENIED_SECURITY,
    "OUTCOME_NONE_CLARIFICATION": Outcome.OUTCOME_NONE_CLARIFICATION,
    "OUTCOME_NONE_UNSUPPORTED": Outcome.OUTCOME_NONE_UNSUPPORTED,
    "OUTCOME_ERR_INTERNAL": Outcome.OUTCOME_ERR_INTERNAL,
}


def _serialize_messages(messages: list) -> list:
    return [
        {**m, "content": [b.model_dump() if hasattr(b, "model_dump") else b for b in m["content"]]}
        if isinstance(m.get("content"), list) else m
        for m in messages
    ]


def _dispatch_tool(vm: PcmRuntimeClientSync, tool_name: str, tool_input: dict) -> str:
    try:
        if tool_name == "context":
            return json.dumps(MessageToDict(vm.context(ContextRequest())), indent=2)
        elif tool_name == "tree":
            return json.dumps(MessageToDict(vm.tree(TreeRequest(
                root=tool_input.get("root", ""),
            ))), indent=2)
        elif tool_name == "find":
            kind_map = {"all": 0, "files": 1, "dirs": 2}
            return json.dumps(MessageToDict(vm.find(FindRequest(
                name=tool_input["name"],
                root=tool_input.get("root", "/"),
                type=kind_map.get(tool_input.get("kind", "all"), 0),
                limit=tool_input.get("limit", 10),
            ))), indent=2)
        elif tool_name == "search":
            return json.dumps(MessageToDict(vm.search(SearchRequest(
                root=tool_input.get("root", "/"),
                pattern=tool_input["pattern"],
                limit=tool_input.get("limit", 10),
            ))), indent=2)
        elif tool_name == "list":
            return json.dumps(MessageToDict(vm.list(ListRequest(name=tool_input["path"]))), indent=2)
        elif tool_name == "read":
            return vm.read(ReadRequest(
                path=tool_input["path"],
            )).content
        elif tool_name == "write":
            return json.dumps(MessageToDict(vm.write(WriteRequest(
                path=tool_input["path"],
                content=tool_input["content"].rstrip("\n"),
            ))), indent=2)
        elif tool_name == "delete":
            return json.dumps(MessageToDict(vm.delete(DeleteRequest(path=tool_input["path"]))), indent=2)
        elif tool_name == "mkdir":
            return json.dumps(MessageToDict(vm.mk_dir(MkDirRequest(path=tool_input["path"]))), indent=2)
        elif tool_name == "move":
            return json.dumps(MessageToDict(vm.move(MoveRequest(
                from_name=tool_input["from_name"], to_name=tool_input["to_name"]
            ))), indent=2)
        else:
            return f"ERROR: unknown tool {tool_name}"
    except ConnectError as e:
        return f"ERROR {e.code}: {e.message}"


class ReActLoopStage:
    def __init__(self, vm: PcmRuntimeClientSync, client: anthropic.Anthropic, model: str,
                 prompt_manager: PromptManager):
        self._vm = vm
        self._client = client
        self._model = model
        self._prompt_manager = prompt_manager
        self._validator = ActionValidator()

    def execute(self, ctx: PipelineContext, logger) -> None:
        prompt = build_initial_user_message(
            ctx.task, ctx.agents_md, ctx.agents_md_path,
            ctx.dfs_tree, ctx.preread_files, ctx.past_mistakes,
            task_plan=ctx.task_plan,
            context_blocks=ctx.context_blocks,
        )
        messages = [{"role": "user", "content": prompt}]
        step_count = 0
        completed = False
        max_steps = ctx.react_max_steps

        while step_count < max_steps:
            ctx.node_counter += 1
            response = self._client.messages.create(
                model=self._model,
                max_tokens=4096,
                system=self._prompt_manager.get("system"),
                tools=TOOLS,
                messages=messages,
            )

            logger.append_api_call(build_api_log_entry(
                "react", self._model, self._prompt_manager.get("system"),
                _serialize_messages(messages), response,
                step=step_count, node_id=str(ctx.node_counter),
            ))
            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason != "tool_use":
                break

            valid_tool_results = []
            ephemeral_tool_results = []
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
                step_count += 1
                print(f"Step {step_count}: {tool_name}", end=" ")

                # ── Pre-execution validation (T5) ──
                is_valid, val_reason = self._validator.validate(tool_name, tool_input, ctx)
                if not is_valid:
                    print(f"{CLI_RED}validation failed: {val_reason}{CLI_CLR}")
                    # Ephemeral: rejection feedback must NOT persist in message history
                    ephemeral_tool_results.append({
                        "type": "tool_result", "tool_use_id": tool_use_id,
                        "content": f"Validation failed: {val_reason}. Correct and retry.",
                    })
                    continue

                if tool_name == "report_completion":
                    message = (tool_input.get("message") or "").strip()
                    outcome_str = tool_input.get("outcome", "OUTCOME_OK")
                    grounding_refs = tool_input.get("grounding_refs") or []
                    if isinstance(grounding_refs, str):
                        try:
                            grounding_refs = json.loads(grounding_refs)
                        except Exception:
                            grounding_refs = []
                    grounding_refs = [r.lstrip("/") for r in grounding_refs if isinstance(r, str)]
                    outcome_proto = OUTCOME_BY_NAME.get(outcome_str, Outcome.OUTCOME_OK)

                    # Empty message is now caught by validator above, but keep as safety net
                    if not message:
                        result_text = "ERROR: message is empty. Re-read relevant files and provide a non-empty message."
                        print(f"{CLI_RED}empty message{CLI_CLR}")
                    else:
                        try:
                            self._vm.answer(AnswerRequest(
                                message=message, outcome=outcome_proto, refs=grounding_refs
                            ))
                            ctx.final_answer = message
                            ctx.final_code = outcome_str
                            result_text = f"Task submitted successfully. Outcome: {outcome_str}. Message: {message}"
                            completed = True
                            print(f"{CLI_GREEN}{message}{CLI_CLR}")
                            for ref in grounding_refs:
                                print(f"- {CLI_GREEN}{ref}{CLI_CLR}")
                        except ConnectError as e:
                            result_text = f"ERROR submitting answer {e.code}: {e.message}"
                            print(f"{CLI_RED}{result_text}{CLI_CLR}")

                    step_record = {
                        "step": step_count, "node_id": str(ctx.node_counter),
                        "cmd": tool_name, "args": tool_input,
                        "result": result_text, "ts": time.time(),
                    }
                    ctx.react_trace.append(step_record)
                    logger.append_react_step(step_record)
                    valid_tool_results.append({"type": "tool_result", "tool_use_id": tool_use_id, "content": result_text})
                    continue

                result_text = _dispatch_tool(self._vm, tool_name, tool_input)
                print(f"{CLI_GREEN}ok{CLI_CLR}")

                step_record = {
                    "step": step_count, "node_id": str(ctx.node_counter),
                    "cmd": tool_name, "args": tool_input,
                    "result": result_text, "ts": time.time(),
                }
                ctx.react_trace.append(step_record)
                logger.append_react_step(step_record)

                if tool_name in ("read", "write"):
                    path = tool_input.get("path")
                    if path and path not in ctx.files_used:
                        ctx.files_used.append(path)

                # T4: external plan progress tracking
                if ctx.task_plan:
                    self._update_plan_progress(ctx, tool_name, step_count)

                # Inject step budget warning when approaching the limit
                if step_count >= max_steps - 3:
                    result_text += (
                        f"\n\n⚠️ STEP BUDGET WARNING: You have used {step_count}/{max_steps} steps. "
                        "You MUST call report_completion on your NEXT action. "
                        "Summarize what you've done and choose the correct OUTCOME code."
                    )

                valid_tool_results.append({"type": "tool_result", "tool_use_id": tool_use_id, "content": result_text})

            # ── BP4: Log per-turn agent reasoning (text blocks) ──
            if step_reasoning.strip():
                logger.append_react_step({
                    "type": "reasoning",
                    "node_id": str(ctx.node_counter),
                    "text": step_reasoning,
                    "ts": time.time(),
                })

            # ── BP3: Ephemeral validation feedback ──
            # Valid results are appended to persistent history; ephemeral rejections are not.
            if valid_tool_results:
                messages.append({"role": "user", "content": valid_tool_results})
                self._maybe_compress_reads(messages, ctx)

            if ephemeral_tool_results:
                # One-shot correction turn: send rejection feedback without persisting it.
                # This lets the model self-correct without polluting the conversation history.
                correction_messages = messages + [{"role": "user", "content": ephemeral_tool_results}]
                ctx.node_counter += 1
                correction_response = self._client.messages.create(
                    model=self._model,
                    max_tokens=4096,
                    system=self._prompt_manager.get("system"),
                    tools=TOOLS,
                    messages=correction_messages,
                )
                logger.append_api_call(build_api_log_entry(
                    "react_correction", self._model, self._prompt_manager.get("system"),
                    _serialize_messages(correction_messages), correction_response,
                    step=step_count, node_id=str(ctx.node_counter),
                ))
                # Append the correction response to persistent history and continue the loop
                messages.append({"role": "assistant", "content": correction_response.content})
                # If the correction itself triggered tool_use, the next while-loop iteration
                # will process it via the regular path (correction_response is not re-entered here).
                # For now, handle the non-tool_use case (text-only correction):
                if correction_response.stop_reason != "tool_use":
                    break
                # Re-enter the loop body for the correction's tool calls on next iteration
                # by synthesising a synthetic response: swap response for correction_response.
                # We continue the outer while loop from the top — the assistant message was
                # already appended above, so the next iteration will call the API with the
                # updated messages list.

            if completed:
                break

        if not ctx.final_answer:
            print(f"{CLI_RED}No answer produced after {step_count} steps{CLI_CLR}")

    def _maybe_compress_reads(self, messages: list, ctx) -> None:
        """Compress last N consecutive read tool_results into a summary entry.

        Scans back through message history collecting consecutive user messages
        that contain only 'read' tool_results. When the total number of such
        entries reaches _COMPRESSION_THRESHOLD they are replaced with a single
        compressed placeholder to prevent unbounded context growth during
        read-heavy exploration phases.
        """
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
        consecutive_read_msgs: list[int] = []  # indices into messages
        for idx in range(len(messages) - 1, -1, -1):
            msg = messages[idx]
            if msg.get("role") != "user":
                break
            content = msg.get("content", [])
            if not isinstance(content, list) or not content:
                break
            # All entries in this user message must be tool_results from read calls
            all_reads = all(
                (entry.get("type") == "tool_result" and entry.get("tool_use_id") in read_ids)
                for entry in content
            )
            if not all_reads:
                break
            consecutive_read_msgs.append(idx)

        if len(consecutive_read_msgs) < _COMPRESSION_THRESHOLD:
            return

        # Collect paths that were read (from ctx.files_used or result content)
        # Use ctx.files_used which is already tracked; take the last N paths
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

        # Remove the consecutive read user messages and replace with one compressed turn
        indices_to_remove = sorted(consecutive_read_msgs)
        for idx in reversed(indices_to_remove):
            messages.pop(idx)
        # Insert compressed message at the earliest removed position
        insert_at = indices_to_remove[0]
        messages.insert(insert_at, {"role": "user", "content": [compressed_entry]})

    @staticmethod
    def _update_plan_progress(ctx: PipelineContext, tool_name: str, step_count: int) -> None:
        """Mark the next incomplete plan step as done if the tool matches expected_tools."""
        for entry in ctx.plan_progress:
            if entry["done"]:
                continue
            plan_step = next(
                (s for s in ctx.task_plan.steps if s.id == entry["step_id"]), None
            )
            if plan_step and tool_name in plan_step.expected_tools:
                entry["done"] = True
                entry["completed_at_react_step"] = step_count
            break  # only check the next incomplete step
