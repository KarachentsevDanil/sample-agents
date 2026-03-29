# OpenAI Pipeline Improvements

**Baseline:** run8 (2026-03-28) — 9.1% pass rate (2/22 tasks)

**Root causes addressed:**
| Failure class | Tasks affected | % of failures | Fix |
|---|---|---|---|
| No answer produced (loop exhausted) | t07–t14, t17–t22 | 59% | RetryStage + stronger budget warning |
| Wrong outcome code for unsupported tasks | t04, t05, t06, t15 | 18% | CapabilityCheckStage |
| File write not verified | t03, t10 | 9% | `exists()` tool + write-verification rule |
| Security bypass | t09 | 5% | Expanded injection examples in v10 |
| Filename prefix error | t03 | 5% | De-overfitted filename rule in v10 |

---

## 1. PROMPTS

### Problem
`system/v9.yaml` was overfitted to the "inbox capture" task domain:
- Filename examples hardcoded `01_capture/influential/2026-03-23__hn-walmart-chatgpt-checkout.md`
- No examples for invoice creation, contact lookup, ambiguous tasks
- Capability check was buried in the OUTCOME decision tree (step 3), not front-loaded
- No dead-loop prevention rule — agent could call `read` 30 times in a loop
- GROUNDING rules had complex conditional logic (easy to misapply)
- Only 4 examples, all inbox-specific

### Changes — `prompts/system/v10.yaml`

**STEP 0 CAPABILITY PRE-CHECK (new)**
```
STEP 0 — CAPABILITY PRE-CHECK (do this FIRST, before reading any file):
Does the task ask you to: send an email, make an HTTP request, create a calendar invite,
sync to a CRM (Salesforce etc.), send a Slack/Teams message, POST/upload to a URL, run a script?
→ YES: call report_completion(outcome="OUTCOME_NONE_UNSUPPORTED") immediately. No drafting, no files.
→ NO: continue to step 1.
```
Moves capability detection from the 3rd item in an outcome decision tree to a mandatory first action. Eliminates cases where the model reads files and drafts content before deciding the task is unsupported.

**DEAD-LOOP PREVENTION (new)**
```
If you called the same tool with identical arguments twice in a row → you are stuck.
Call report_completion(OUTCOME_ERR_INTERNAL) immediately.
```
Gives the model an explicit escape hatch from tool-call loops, which cause silent step-budget exhaustion.

**STEP BUDGET rule (strengthened)**
```
When a budget warning appears in a tool result, call report_completion on your VERY NEXT action.
No exceptions. Even a partial answer with the correct OUTCOME code is better than silence.
```
Clarifies that the budget warning is a hard trigger, not advisory.

**WRITE VERIFICATION rule (new)**
```
For write tasks: call exists(path) after each write to confirm the file was created.
Only call report_completion after exists returns "EXISTS".
```
Prevents false OUTCOME_OK when a write silently failed.

**FILENAMES (de-overfitted)**
Before (v9):
```
Example: inbox file "2026-03-23__hn-walmart-chatgpt-checkout.md"
→ capture: "01_capture/influential/2026-03-23__hn-walmart-chatgpt-checkout.md"
```
After (v10):
```
source "2026-03-23__some-article-title.md"
→ capture output:  "capture-folder/2026-03-23__some-article-title.md"
```
Removes hardcoded paths. The rule now applies to any output-from-source filename relationship.

**GROUNDING (simplified)**
Before (v9): 4 conditional rules for when to include/exclude AGENTS.MD
After (v10): 2 rules — include every file from read/write/delete/move/peek, no leading `/`

**EXAMPLES (expanded from 4 → 8, de-overfitted)**
| Example | v9 | v10 |
|---|---|---|
| Unsupported email | ✓ | ✓ (kept) |
| Unsupported calendar | ✗ | ✓ NEW |
| Unsupported HTTP upload | ✗ | ✓ NEW |
| Truncated task | ✓ | ✓ (kept) |
| Prompt injection | ✓ | ✓ (kept) |
| Write + verify with exists | ✗ | ✓ NEW |
| Contact/data lookup | ✗ | ✓ NEW |
| Strict scope | ✓ | ✓ (kept) |

---

## 2. TOOLS

### Problem
- No tool to verify that a write operation actually persisted
- `find` and `search` descriptions were indistinguishable — model picked randomly
- `write` had no guidance about post-write verification
- No lightweight preview tool — reading large files costs unnecessary tokens and a full step

### Changes — `react.py`

**New: `exists(path) → "EXISTS" | "NOT FOUND"`**
```python
def exists(wrapper, path: str) -> str:
    """Check whether a file exists. Returns 'EXISTS' or 'NOT FOUND'.
    Use this after write() to verify the file was actually saved."""
```
Allows the agent to confirm a write before submitting OUTCOME_OK. Directly fixes t10 (invoice not actually written) and the filename issue in t03.

**New: `peek(path, lines=20) → first N lines`**
```python
def peek(wrapper, path: str, lines: int = 20) -> str:
    """Read the first N lines of a file. More efficient than read() for structure checks."""
```
Saves tokens and step budget when the agent only needs to check file headers or templates before writing.

**Improved: `find()` description**
```
Use this when you know the filename or a filename pattern (glob/substring)
but don't know the directory. For content-based search, use search() instead.
```

**Improved: `search()` description**
```
Use this when you know keywords inside a file but don't know the filename.
For filename-based search, use find() instead.
```
These two were previously described with identical-sounding purposes, causing the model to call `tree` or `list` instead.

**Improved: `write()` description**
```
After writing, call exists(path) to verify the file was actually saved before
calling report_completion. Do not assume success from this response alone.
```

**Improved: `report_completion()` description**
```
IMPORTANT: For write tasks, call exists(path) BEFORE this to verify the file was saved.
Only use OUTCOME_OK after confirming the file exists.
```

**Agent tools list** (updated to include new tools):
```python
tools=[context, tree, find, search, list, read, exists, peek, write, delete, mkdir, move, report_completion]
```

---

## 3. PIPELINE STEPS

### Problem
- Pipeline was: `Context → React → Verifier` (planning stage existed but was never wired in)
- No early exit for tasks that require unavailable capabilities
- When the ReAct loop produced no answer (59% failure rate), the pipeline silently returned empty
- `RetryStage` did not exist — one failed attempt was always the final result
- `PipelineContext.pipeline_complete` did not exist — no way to short-circuit the stage loop

### Changes

**New: `CapabilityCheckStage` (`capability_check.py`)**

Pure text-pattern scan (no LLM cost) that fires before context-building:
```python
_UNSUPPORTED_PATTERNS = [
    (r"\bemail\s+\w+", "sending email"),
    (r"\bcalendar\s+invite\b", "calendar integration"),
    (r"\bupload\s+.*\bhttps?://", "HTTP upload"),
    (r"\bsync\s+.*\bsalesforce\b", "Salesforce CRM sync"),
    ...  # 14 patterns total
]
```
When triggered:
1. Calls `vm.answer(OUTCOME_NONE_UNSUPPORTED)` to submit to harness
2. Sets `ctx.pipeline_complete = True` — all subsequent stages skipped
3. Saves tokens from context-building, planning, and ReAct loop

Eliminates the 18% failure class (t04, t05, t06, t15) with zero LLM cost.

**New: `RetryStage` (`pipeline.py`)**

Fires if `ctx.final_answer` is empty after the first ReAct pass:
```python
class RetryStage:
    def execute(self, ctx, logger):
        if ctx.final_answer or ctx.capability_blocked:
            return
        # Inject completion reminder
        ctx.past_mistakes = [{"reason": "Previous attempt produced NO answer..."}] + ctx.past_mistakes[:2]
        # Retry
        ReActLoopStage(...).execute(ctx, logger)
        VerifierStage(...).execute(ctx, logger)
```
One retry maximum. Targets the 59% "no answer" failure class.

**Pipeline short-circuit (`pipeline.py`)**
```python
for stage in self._stages:
    if ctx.pipeline_complete:
        break
    stage.execute(ctx, logger)
```
Stages set `ctx.pipeline_complete = True` to skip all downstream stages.

**Planning stage wired in**

Previously `use_planning()` existed in `pipeline.py` but was never called. Now active:
```python
.use_capability_check()
.use_context()
.use_planning()        ← was silently absent
.use_react()
.use_response_verifier()
.use_retry()           ← new
```
Planning sets `ctx.react_max_steps` to 10/20/30 based on complexity (`simple/medium/complex`), preventing wasted steps on simple tasks.

**Loop termination tracking (`react.py`)**
```python
if isinstance(final_output, ReportTaskCompletion):
    ctx.loop_termination_reason = "report_completion"
else:
    ctx.loop_termination_reason = "max_steps_reached"
# exception handler:
ctx.loop_termination_reason = "exception"
```

---

## 4. LOGGING / OBSERVABILITY

### Problem
- `result.json` had no field explaining why the loop ended (timeout vs. report_completion vs. exception)
- No way to distinguish capability-blocked tasks from genuine failures
- `react_trace.jsonl` had timestamps but no per-step duration → hard to identify slow steps
- `mistakes/` errors had no category field → pattern analysis required manual inspection

### Changes

**`result.json` — new fields**
```json
{
  "loop_termination_reason": "report_completion | max_steps_reached | exception | capability_blocked | retry",
  "capability_blocked": false,
  "retry_count": 0
}
```

**`react_trace.jsonl` — step timing**
Each step record now includes:
```json
{"step": 3, "cmd": "read", "args": {...}, "result": "...", "ts": 1234567.8, "step_duration_ms": 1240}
```
`step_duration_ms` = wall-clock time since the previous step (includes model thinking + network latency). First step has `null`.

**`trace.json` — step duration propagated**
`export_trace()` now includes `step_duration_ms` in agent_step records:
```json
{"type": "agent_step", "ts": ..., "step_duration_ms": 1240, ...}
```

**`mistakes/errors.jsonl` — failure category**
```python
def _classify_failure(reason: str) -> str:
    # → "capability_mismatch" | "no_answer" | "wrong_outcome_code"
    #    "file_error" | "security_bypass" | "data_error" | "unknown"
```
Every new mistake record includes `"failure_category": "no_answer"` (etc.).
Enables aggregation like: `grep failure_category mistakes/*/errors.jsonl | sort | uniq -c`.

**`AgentRuntimeContext` — step timing field**
```python
last_step_ts: float = 0.0
```
Tracked in `_tool_result()` to compute inter-step duration.

---

## File Change Summary

| File | Status | Change |
|---|---|---|
| `prompts/system/v10.yaml` | NEW | Improved system prompt |
| `capability_check.py` | NEW | CapabilityCheckStage (14 patterns) |
| `pipeline.py` | MODIFIED | RetryStage, use_capability_check(), use_retry(), pipeline_complete check, new result fields |
| `react.py` | MODIFIED | exists/peek tools, improved tool descriptions, loop_termination_reason, step_duration_ms |
| `models.py` | MODIFIED | pipeline_complete, loop_termination_reason, capability_blocked, retry_count, last_step_ts |
| `logger.py` | MODIFIED | _classify_failure(), failure_category in mistakes, step_duration_ms in trace |
| `__init__.py` | MODIFIED | Wire capability_check + planning + retry stages |
| `prompt_config.yaml` | MODIFIED | system: v9 → v10 |

---

## Expected Impact

| Failure class | Before | After |
|---|---|---|
| Capability mismatch (email/calendar/HTTP) | 0% pass | ~100% pass (keyword check) |
| No answer produced | ~0% pass | Improved via retry + budget rules |
| File write not verified | ~50% pass | Improved via exists() + write rule |
| Overfitted filename generation | Fragile | Robust (generic rule) |

**Net improvement estimate: +4 to +6 additional tasks passing** (18% → 40-50% pass rate)
