# Evolve System

## Executive Summary

The current Claude-code pipeline is workable for a small benchmark, but it is not yet shaped for a `100+` tool environment or for reliable operation on small models such as `gpt-5.4-nano`.

The most important issue is architectural, not prompt quality: the current verifier does not influence the submitted result. In the Claude backend, the answer is submitted inside the ReAct loop, then the verifier runs afterward, and the benchmark trial is ended regardless. That makes verification mostly a logging step, not a control step.

The second major issue is context strategy. The pipeline currently does a full DFS-style root walk up front, injects the full filesystem outline into the initial prompt, grows the message history monotonically, and exposes a flat tool surface. That can work with `7` tools and a tiny sandbox, but it will degrade badly with `100+` tools and larger workspaces.

The third issue is observability. The repo has a logger abstraction and docs mention API-call and parse-error logs, but the Claude backend does not log the actual prompts/messages sent to the LLM, does not log parse failures, and does not emit a proper tool-call ledger. That makes debugging prompt regressions and tool-selection failures much harder than it needs to be.

My recommendation is to evolve the system around four principles:

1. Move from prompt-enforced correctness to pipeline-enforced correctness.
2. Move from raw transcript accumulation to tiered context management.
3. Move from flat tool exposure to routed tool menus and macro-tools.
4. Move from raw mistake logs to curated procedural memory.

## What The Current Claude Pipeline Actually Does

These points are based on the current implementation, not on the architecture docs.

- `agent_pipeline_claude/react.py` submits the answer during `report_completion` by calling `self._vm.answer(...)` before verification.
- `agent_pipeline_claude/verifier.py` runs only after `ctx.final_answer` already exists, and if verifier parsing fails it explicitly fails open.
- `main_claude.py` always calls `client.end_trial(...)` after `run_agent(...)`; verifier output does not gate trial completion.
- `agent_pipeline_claude/context.py` performs a full recursive walk from `/`, stores it as `ctx.dfs_tree`, and injects it into the initial prompt.
- `agent_pipeline_claude/react.py` appends all assistant/tool turns to `messages` and never compacts or summarizes them.
- `agent_pipeline/logger.py` supports `api_calls.jsonl` and `llm_parse_errors.jsonl`, but the Claude backend only appends `react_trace.jsonl`; there is no equivalent Claude logging for raw LLM request/response content.
- `mistakes/<task_id>/errors.jsonl` is task-local memory keyed mostly by `reason` strings. It is useful as a scratchpad, but it is not procedural memory yet.

## Findings By Area

### 1. Verification And Final-Answer Control

#### Current weakness

The current verifier is downstream of submission, so it cannot protect quality. It can only annotate failure after the fact.

This is why the verification stage feels obsolete: in the Claude backend, that reading is correct.

There is also a second weakness: even when the verifier runs, it is weakly grounded. The verifier currently sees task text, AGENTS instructions, a short trace summary, and the final answer. It does not validate against a canonical executed-tool ledger or deterministic grounding-ref rules. That makes it both soft and circular.

#### Recommendation

Do not keep verification in its current form.

The best shape is a hybrid:

- Pre-submit deterministic guards.
- Selective LLM review for ambiguous cases.
- Post-submit benchmark/error analysis for learning.

In practice:

- Before submission, run a deterministic `submission_guard`.
- The guard should verify:
  - answer is non-empty
  - required writes happened
  - required named files were read
  - grounding refs match the actual read/write/delete ledger
  - paths are normalized and deduplicated
  - exact-answer constraints are satisfied when AGENTS rules are explicit
- If deterministic checks fail, do not submit. Feed the concrete error back into the agent.
- Only invoke an LLM verifier when the check is semantic or ambiguous, for example:
  - did the answer format satisfy a fuzzy natural-language task?
  - is the selected output file semantically the right one when multiple candidates exist?

#### On the “ground refs checker with separate prompt” idea

I would not make grounding-ref validation primarily prompt-based.

Grounding refs should be derived mechanically from the executed tool ledger. The model should not be trusted to remember every ref in a long session. A separate LLM prompt can be used only for an ambiguity review such as whether a pre-read file materially contributed to the answer, but completeness and normalization should be deterministic.

#### On the “final result verifier” idea

A final verifier still has value, but only if it can change behavior.

Recommended split:

- Blocking verifier before submit:
  - deterministic first
  - LLM only when needed
- Non-blocking verifier after submit:
  - analyze failure reasons
  - extract candidate learnings
  - feed memory/update loops

That preserves the useful part of verification without pretending a post-submit check is a quality gate.

### 2. Context Management For `100+` Tools And Small Models

#### Current weakness

The current context model is expensive and brittle:

- full root DFS is injected up front
- pre-read files are injected verbatim
- every tool result is appended into one growing conversation
- no hot/cold context split
- no compaction
- no tool shortlist

This is exactly the wrong shape for small models. Nano-class models need aggressively curated context, not exhaustive context.

#### Recommendation

Introduce tiered context management.

The pipeline should explicitly maintain:

- Task frame:
  - task
  - success criteria
  - answer contract
  - current plan
- Working set:
  - currently relevant files
  - currently relevant tool family
  - latest evidence
- Long-term run memory:
  - compact summaries of completed tool actions
  - important discovered facts
  - unresolved questions
- Cold storage:
  - raw tool outputs
  - raw file contents
  - full transcript

Only the task frame and current working set should stay in the live prompt by default.

#### Recommended compaction policy

Enable compaction as a first-class runtime behavior, not as an afterthought.

Trigger compaction on:

- token threshold
- step threshold
- large tool output
- long-running task duration

Compaction output should be structured, not free-form. Preserve:

- files read/written/deleted
- key facts extracted from each file
- outstanding requirements
- rejected hypotheses
- pending next actions
- exact strings that must remain verbatim

Do not compact away:

- answer contract
- file-operation ledger
- required refs
- created/modified path list
- benchmark-specific constraints

#### Recommended architecture for small models

For `100+` tools, do not expose a flat tool list to the main model.

Use a layered tool system:

1. Tool registry with metadata
2. Lightweight tool router
3. Top-k tool shortlist injected into the acting prompt
4. Tool-family adapters
5. Macro-tools for common workflows

Tool metadata should include:

- capability family
- path/domain scope
- cost
- latency
- typical usage examples
- failure modes
- output size

For a small model, the main agent should usually see only:

- 3 to 10 candidate tools
- short examples for those tools
- a compact state summary

That matters more than prompt wording.

### 3. Tooling Strategy

#### Current weakness

The current `7` tools are simple and understandable, but the pipeline already assumes the model can orchestrate them directly. That does not scale well.

As tool count rises, the model’s main job should become choosing a workflow, not micro-assembling every filesystem step from primitives.

#### Recommendation

Add higher-level custom tools that reduce prompt burden and compress decision-making.

Good additions:

- `dfs(path, depth, include_files, include_dirs)`
- `batch_read(paths[])`
- `search_and_peek(path, pattern, count, context_lines)`
- `read_manifest(path)` for structured folders
- `read_relevant(paths[], task_hint)` for router-selected bundles
- `finalize_answer(answer)` that auto-attaches deterministic refs from the ledger

The DFS idea is especially good, but it should be scoped. Full-root DFS should not be the default. Use path-scoped discovery and lazy expansion instead.

The most important tool improvement is not “more tools”. It is “better abstraction boundaries”.

### 4. Logging And Observability

#### Current weakness

Current observability is not sufficient for prompt/system debugging.

Most importantly, you do not log the actual messages sent to the LLM. That makes it hard to debug:

- prompt regressions
- context bloat
- tool-menu confusion
- verifier behavior
- parse/format failures

There is also drift between docs and implementation. The repo documents parse-error and API-call logs, but the Claude backend does not currently emit that level of detail.

#### Recommendation

Add a dedicated `llm_events.jsonl` stream.

Each event should capture:

- stage
- request type
- model
- prompt version ids
- system prompt digest and optionally full content
- message list digest and optionally full content
- tool menu presented to the model
- token counts
- latency
- response stop reason
- raw response blocks
- parse status
- validation errors

Recommended log levels:

- `summary`: safe default in normal runs
- `full`: includes complete messages for debugging
- `redacted-full`: full structure with sensitive payloads redacted

Also add a proper executed-tool ledger:

- tool name
- args
- normalized result
- error class
- latency
- bytes returned
- files touched

This ledger should become the source of truth for:

- grounding refs
- verification checks
- memory extraction
- failure analysis

### 5. Procedural Memory

#### Current weakness

The current mistake store is useful but primitive:

- scoped by `task_id`
- mostly raw benchmark/verifier reasons
- deduplicated by exact reason string
- no retrieval by similarity
- no distinction between noise and durable learning
- no conversion into reusable procedure

This is not yet “learn from previous errors” in the staff-engineering sense.

#### Recommendation

Promote mistakes into procedural memory with a stricter schema.

Memory entry shape:

- trigger
- anti-pattern
- correct procedure
- evidence
- scope
- confidence
- source
- benchmark impact
- expiry/revalidation policy

Example:

- Trigger: write task involving invoice numbering
- Anti-pattern: preserve template but leave trailing newline
- Correct procedure: when cloning template-based files, normalize trailing newline exactly to source contract
- Scope: file-creation tasks with exact body scoring

Retrieval should be by:

- task similarity
- tool pattern similarity
- file/domain similarity
- error-class similarity

Only high-signal memories should be injected into live context. Everything else should remain in the background store.

#### How to build the loop

Recommended flow:

1. Run task
2. Compare benchmark result with expected success
3. If failure, classify root cause
4. Distill candidate memory
5. Re-run on held-out tasks or prior failures
6. Promote memory only if it improves results without regression

This is where the post-submit verifier is useful: as a memory distillation input, not as the main quality gate.

### 6. Error Recovery

#### Current weakness

The pipeline mostly treats tool failures as text returned to the model. That is simple, but not robust enough.

There is no explicit failure taxonomy or recovery policy by error type.

#### Recommendation

Introduce structured recovery paths.

Classify failures into:

- transient transport
- invalid path
- permission/contract violation
- oversized output
- parse failure
- hallucinated tool usage
- benchmark constraint failure

Then define policy:

- retry automatically
- compact and continue
- ask model to repair plan
- route to stronger model
- stop early and surface a hard failure

For long-running calls, compaction should happen automatically after the result is persisted. The live prompt should get a summary plus a handle to the raw artifact, not the full raw payload.

### 7. Code Structure And Maintainability

#### Current weakness

The repo has multiple backend implementations with overlapping logic and visible drift. The architecture docs describe behaviors that are present in some backends but not in Claude. That is a maintainability smell.

The result is that:

- fixes do not automatically propagate across backends
- documentation becomes stale
- behavior differences are accidental, not strategic

#### Recommendation

Refactor around shared pipeline contracts and backend adapters.

Good split:

- shared pipeline state model
- shared execution ledger
- shared verification/guard framework
- shared memory interfaces
- backend-specific LLM adapter
- backend-specific tool transport adapter

The Claude/OpenAI/LangChain/LangGraph variants should differ at the adapter layer, not at the workflow semantics layer.

This also makes it much easier to run systematic experiments.

### 8. Self-Improving Loop

#### Current weakness

A self-improving loop is directionally correct, but it is dangerous if it directly mutates prompts or behaviors from sparse evidence.

#### Recommendation

Keep the self-improving loop offline and evaluation-gated.

Good loop:

- collect failures
- cluster them
- synthesize candidate interventions
- run ablations
- compare against baseline
- promote only proven improvements

Candidate interventions can include:

- new procedural memories
- new deterministic guards
- better tool metadata
- new macro-tools
- prompt edits
- tool-routing changes

Do not allow automatic prompt mutation in the mainline runtime without eval gating.

## Prioritized Roadmap

### P0: Highest leverage

- Replace post-submit verifier with a true pre-submit `submission_guard`.
- Auto-build grounding refs from the executed-tool ledger.
- Add `llm_events.jsonl` with actual request/response logging.
- Add transcript compaction with protected state fields.
- Stop exposing a flat tool list once tool count grows beyond a small threshold.

### P1: Next

- Add routed tool menus and tool metadata.
- Introduce macro-tools such as scoped DFS, batch read, and search-and-peek.
- Build procedural memory from benchmark failures.
- Add structured failure taxonomy and recovery policies.
- Refactor shared pipeline semantics out of backend-specific copies.

### P2: After the foundations are solid

- Add selective LLM adjudication for ambiguous semantic checks.
- Add offline self-improvement/eval loop.
- Add model routing by task/tool complexity.
- Add benchmark analytics and failure clustering dashboards.

## Recommended Target Design

If I were evolving this as a staff engineer, I would target this shape:

1. Planner/router stage
   - picks tool family
   - picks small working set
   - picks top-k candidate tools

2. Acting stage
   - runs with compact context
   - writes every action to a canonical ledger

3. Submission guard
   - deterministic checks first
   - optional LLM review only for ambiguity

4. Submission
   - answer and refs emitted from canonical state, not freehand model memory

5. Postmortem stage
   - benchmark result ingestion
   - failure classification
   - procedural memory candidate extraction

That would make verification meaningful, make small models viable, and keep the system debuggable as tool count and task complexity increase.

## Bottom Line

The right next move is not “make the verifier smarter”. It is “move correctness into the pipeline”.

For this codebase, I would do the following first:

- make submission blocking on deterministic correctness checks
- derive refs from execution, not from model recall
- log the actual LLM messages
- compact aggressively
- route tools instead of flattening them
- turn mistake logs into procedural memory

That set of changes will improve both present benchmark performance and future scalability much more than another round of prompt tweaking.
