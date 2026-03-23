import json
import time

from connectrpc.errors import ConnectError
from google.protobuf.json_format import MessageToDict
from openai import OpenAI

from bitgn.vm.mini_connect import MiniRuntimeClientSync
from bitgn.vm.mini_pb2 import (
    AnswerRequest,
    DeleteRequest,
    ListRequest,
    OutlineRequest,
    ReadRequest,
    SearchRequest,
    WriteRequest,
)

from .models import (
    NextStep,
    PipelineContext,
    ReportTaskCompletion,
    Req_Delete,
    Req_List,
    Req_Read,
    Req_Search,
    Req_Tree,
    Req_Write,
    VerificationResult,
)
from .prompts import SYSTEM_PROMPT, VERIFIER_PROMPT, build_initial_user_message

MAX_STEPS = 30
MAX_LLM_RETRIES = 3
MAX_VERIFY_RETRIES = 2

CLI_RED = "\x1B[31m"
CLI_GREEN = "\x1B[32m"
CLI_CLR = "\x1B[0m"


class ReActLoopStage:
    def __init__(self, vm: MiniRuntimeClientSync, client: OpenAI, model: str):
        self._vm = vm
        self._client = client
        self._model = model
        self._verify_attempts = 0

    def execute(self, ctx: PipelineContext, logger) -> None:
        self._verify_attempts = 0
        log = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_initial_user_message(
                ctx.task, ctx.agents_md, ctx.agents_md_path, ctx.dfs_tree,
                ctx.preread_files, ctx.past_mistakes,
            )},
        ]

        for step_idx in range(MAX_STEPS):
            print(f"Next step_{step_idx + 1}... ", end="")

            job = self._llm_parse(log, logger)
            if job is None:
                break

            print(job.plan_remaining_steps_brief[0], f"\n  {job.function}")

            if isinstance(job.function, ReportTaskCompletion):
                if not job.function.answer.strip():
                    log.append({"role": "user", "content": (
                        "ERROR: answer is empty. Re-read relevant files and provide a complete non-empty answer."
                    )})
                    continue

                if self._verify_attempts < MAX_VERIFY_RETRIES:
                    verdict = self._inline_verify(ctx, job.function)
                    if verdict is not None and not verdict.passed:
                        self._verify_attempts += 1
                        log.append({"role": "user", "content": (
                            f"Pre-submission check failed: {verdict.reason}\n"
                            "Re-examine the task requirements and AGENTS.md, then try again."
                        )})
                        continue

            log.append({"role": "assistant", "content": job.model_dump_json()})

            try:
                result = self._dispatch(job.function)
                result_dict = MessageToDict(result)
                txt = json.dumps(result_dict, indent=2)
                print(f"{CLI_GREEN}OUT{CLI_CLR}: {txt}")
                api_record = {
                    "step": step_idx + 1,
                    "cmd": job.function.__class__.__name__,
                    "args": job.function.model_dump(),
                    "result": result_dict,
                    "ts": time.time(),
                }
            except ConnectError as e:
                txt = e.message
                print(f"{CLI_RED}ERR {e.code}: {e.message}{CLI_CLR}")
                api_record = {
                    "step": step_idx + 1,
                    "cmd": job.function.__class__.__name__,
                    "args": job.function.model_dump(),
                    "error": e.message,
                    "code": str(e.code),
                    "ts": time.time(),
                }

            logger.append_api_call(api_record)

            step_record = {
                "step": step_idx + 1,
                "current_state": job.current_state,
                "plan": job.plan_remaining_steps_brief,
                "function": job.function.__class__.__name__,
                "result_summary": txt[:400],
                "ts": time.time(),
            }
            ctx.react_trace.append(step_record)
            logger.append_react_step(step_record)

            if isinstance(job.function, (Req_Read, Req_Write)):
                ctx.files_used.append(job.function.path)

            if isinstance(job.function, ReportTaskCompletion):
                ctx.final_answer = job.function.answer
                ctx.final_code = job.function.code
                print(f"{CLI_GREEN}agent {job.function.code}{CLI_CLR}. Summary:")
                for s in job.function.completed_steps_laconic:
                    print(f"- {s}")
                print(f"\n{CLI_GREEN}AGENT ANSWER: {job.function.answer}{CLI_CLR}")
                if job.function.grounding_refs:
                    for ref in job.function.grounding_refs:
                        print(f"- {CLI_GREEN}{ref}{CLI_CLR}")
                break

            log.append({"role": "user", "content": (
                f"[Tool result for {job.function.__class__.__name__}]:\n{txt}"
            )})

    def _llm_parse(self, log: list, logger=None) -> NextStep | None:
        schema_hint = json.dumps(NextStep.model_json_schema(), indent=2)
        for attempt in range(MAX_LLM_RETRIES):
            resp = None
            try:
                resp = self._client.beta.chat.completions.parse(
                    model=self._model,
                    response_format=NextStep,
                    messages=log,
                    max_completion_tokens=16384,
                )
                job = resp.choices[0].message.parsed
                if job is not None:
                    return job
                raise ValueError("parsed is None")
            except Exception as e:
                raw_content = None
                if resp is not None:
                    try:
                        raw_content = resp.choices[0].message.content
                    except Exception:
                        pass
                if logger is not None:
                    logger.append_llm_parse_error({
                        "attempt": attempt + 1,
                        "error": str(e),
                        "raw_content": raw_content,
                        "ts": time.time(),
                    })
                if raw_content:
                    try:
                        obj, _ = json.JSONDecoder().raw_decode(raw_content.strip())
                        return NextStep.model_validate(obj)
                    except Exception as fe:
                        if logger is not None:
                            logger.append_llm_parse_error({
                                "attempt": attempt + 1,
                                "stage": "fallback",
                                "error": str(fe),
                                "raw_content": raw_content,
                                "ts": time.time(),
                            })
                if attempt == MAX_LLM_RETRIES - 1:
                    print(f"{CLI_RED}All retries failed: {e}{CLI_CLR}")
                    return None
                log.append({"role": "user", "content": (
                    f"Your previous response was not valid JSON. "
                    f"You MUST respond with a single JSON object matching this schema:\n{schema_hint}"
                )})
        return None

    def _inline_verify(self, ctx: PipelineContext, completion: ReportTaskCompletion) -> VerificationResult | None:
        trace_summary = "\n".join(
            f"Step {s['step']}: {s['function']} — {s.get('result_summary', '')[:120]}"
            for s in ctx.react_trace
        ) or "(no steps yet)"
        messages = [
            {"role": "system", "content": VERIFIER_PROMPT},
            {"role": "user", "content": (
                f"Task: {ctx.task}\n\n"
                f"AGENTS.md instructions:\n{ctx.agents_md}\n\n"
                f"Reasoning trace summary:\n{trace_summary}\n\n"
                f"Final answer: {completion.answer}\n"
                f"Grounding refs: {completion.grounding_refs}"
            )},
        ]
        try:
            resp = self._client.beta.chat.completions.parse(
                model=self._model,
                response_format=VerificationResult,
                messages=messages,
                max_completion_tokens=512,
            )
            return resp.choices[0].message.parsed
        except Exception:
            return None

    def _dispatch(self, cmd):
        if isinstance(cmd, Req_Tree):
            return self._vm.outline(OutlineRequest(path=cmd.path))
        if isinstance(cmd, Req_Search):
            return self._vm.search(SearchRequest(path=cmd.path, pattern=cmd.pattern, count=cmd.count))
        if isinstance(cmd, Req_List):
            return self._vm.list(ListRequest(path=cmd.path))
        if isinstance(cmd, Req_Read):
            return self._vm.read(ReadRequest(path=cmd.path))
        if isinstance(cmd, Req_Write):
            return self._vm.write(WriteRequest(path=cmd.path, content=cmd.content))
        if isinstance(cmd, Req_Delete):
            return self._vm.delete(DeleteRequest(path=cmd.path))
        if isinstance(cmd, ReportTaskCompletion):
            return self._vm.answer(AnswerRequest(answer=cmd.answer, refs=cmd.grounding_refs))
        raise ValueError(f"Unknown command: {cmd}")
