# Architecture

## Overview

This project is a multi-backend agent framework. Given a natural-language task, an agent interacts with a sandboxed virtual machine (VM) via typed tool calls, then produces a verifiable answer. The answer is scored by an external harness (score 0.0–1.0). The goal is **100% score** across all benchmark tasks.

Three non-negotiable design requirements drive every architectural decision:

1. **Grounded reasoning** — the agent must read real files, not hallucinate content
2. **Correctness enforcement** — answers are validated against explicit rules before leaving the pipeline
3. **Failure memory** — mistakes on a task must not be repeated on the next run

---

## Multi-Backend Design

The same three-stage pipeline structure is implemented for five LLM backends, all sharing models, prompts, and the VM interface:

| Backend ID | Module | Entry Script |
|---|---|---|
| `legacy` | `agent.py` (single-file, OpenAI structured output) | `run.sh` |
| `agent_pipeline` | `agent_pipeline/` (reference implementation) | `run.sh --pipeline agent_pipeline` |
| `openai_agents` | `agent_pipeline_openai/` | `run_openai.sh` |
| `langchain` | `agent_pipeline_langchain/` | `run_langchain.sh` |
| `langgraph` | `agent_pipeline_langgraph/` | `run_langgraph.sh` |
| `claude_cli` | `agent_pipeline_claude/` | `run_claude.sh` |

**Entry point:** `main.py` dispatches to the correct backend based on `--pipeline` argument. `main_claude.py` is a simpler Claude-only entry point used directly by `run_claude.sh`.

---

## Three-Stage Pipeline

```
┌─────────────────────────────────────────────────────────────────────┐
│                          run_agent()                                 │
│                  agent_pipeline_claude/__init__.py                   │
└─────────────────────┬───────────────────────────────────────────────┘
                      │  builds & runs
                      ▼
┌─────────────────────────────────────────────────────────────────────┐
│                           Pipeline                                   │
│                  agent_pipeline_claude/pipeline.py                   │
│                                                                      │
│   ┌────────────────┐  ┌────────────────┐  ┌────────────────────┐   │
│   │  Stage 1       │  │  Stage 2       │  │  Stage 3           │   │
│   │  Context       │─▶│  ReAct Loop    │─▶│  Verifier          │   │
│   │  Builder       │  │                │  │                    │   │
│   └────────────────┘  └────────────────┘  └────────────────────┘   │
│                                                                      │
│                    shared: PipelineContext                           │
│                    shared: RunLogger                                 │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ calls
                    ┌──────────┴────────────┐
                    │                       │
             ┌──────▼──────┐       ┌────────▼───────┐
             │ Anthropic   │       │  MiniRuntime   │
             │ API (LLM)   │       │  VM (gRPC/     │
             └─────────────┘       │  ConnectRPC)   │
                                   └────────────────┘
```

### Stage 1 — ContextBuilderStage (`agent_pipeline_claude/context.py`)

Pre-loads knowledge before the ReAct loop starts:

1. Fetch `AGENTS.md` from the VM filesystem
2. Fetch DFS tree of `/` from VM (`vm.outline`)
3. LLM call → `FileSuggestion` (up to 8 files to pre-read)
4. Read suggested files from VM
5. Load past mistakes from `mistakes/<task_id>/errors.jsonl`

All results are stored in `PipelineContext` and injected into the initial ReAct prompt.

### Stage 2 — ReActLoopStage (`agent_pipeline_claude/react.py`)

Core reasoning loop (max 30 steps):

```
for step in range(30):
    LLM call → NextStep (structured output)
    if function == ReportTaskCompletion:
        inline_verify() → must pass before committing
    dispatch function → VM tool call
    append result to conversation
    if ReportTaskCompletion accepted: break
```

LLM parse is retried up to 3 times with schema correction hints on failure. A `raw_decode` fallback recovers near-miss JSON responses.

### Stage 3 — VerifierStage (`agent_pipeline_claude/verifier.py`)

Final quality gate after the loop:

- If no answer produced → record failure
- LLM call → `VerificationResult { passed, reason }`
- If failed → append mistake to `mistakes/<task_id>/errors.jsonl`
- Fail-open policy: verifier crash → default `passed=True`

---

## Key Components

| Module | File | Role |
|---|---|---|
| Entry point | `agent_pipeline_claude/__init__.py` | `run_agent()` fluent builder |
| Pipeline | `agent_pipeline_claude/pipeline.py` | Stage composition + RunLogger lifecycle |
| Context stage | `agent_pipeline_claude/context.py` | Pre-load AGENTS.md, tree, files, mistakes |
| ReAct loop | `agent_pipeline_claude/react.py` | LLM loop, tool dispatch, inline verify |
| MCP tools | `agent_pipeline_claude/mcp_vm.py` | Wraps VM tools as MCP server for Claude SDK |
| Verifier | `agent_pipeline_claude/verifier.py` | Post-loop quality gate + mistake recorder |
| Data models | `agent_pipeline/models.py` | Pydantic models: NextStep, Req_*, PipelineContext |
| Prompts | `agent_pipeline/prompts.py` | SYSTEM_PROMPT, VERIFIER_PROMPT, build_initial_user_message |
| Logger | `agent_pipeline/logger.py` | RunLogger, log_benchmark_result |
| REST proxy | `app.py` | Flask server proxying harness + VM over HTTP |

---

## PipelineContext Data Flow

```
┌─────────────────────────────────────────────────────┐
│                   PipelineContext                    │
├─────────────────┬───────────────────────────────────┤
│  Inputs         │  task: str                         │
│  (set at init)  │  model: str                        │
├─────────────────┼───────────────────────────────────┤
│  Stage 1 fills  │  agents_md: str                    │
│                 │  agents_md_path: str               │
│                 │  dfs_tree: str (JSON)              │
│                 │  preread_files: dict[path, content]│
│                 │  past_mistakes: list               │
├─────────────────┼───────────────────────────────────┤
│  Stage 2 fills  │  react_trace: list[step_record]   │
│                 │  files_used: list[str]             │
│                 │  final_answer: str                 │
│                 │  final_code: str                   │
├─────────────────┼───────────────────────────────────┤
│  Stage 3 fills  │  verification_passed: bool         │
│                 │  verification_reason: str          │
└─────────────────┴───────────────────────────────────┘
```

Stages are decoupled — Stage 2 only reads what Stage 1 wrote into `ctx`. No direct stage-to-stage imports.

---

## Tool Dispatch

The `NextStep.function` field is a discriminated union of 7 types. Each maps to a VM call:

```python
Req_Tree    → vm.outline(OutlineRequest(path))
Req_Search  → vm.search(SearchRequest(path, pattern, count))
Req_List    → vm.list(ListRequest(path))
Req_Read    → vm.read(ReadRequest(path))
Req_Write   → vm.write(WriteRequest(path, content))
Req_Delete  → vm.delete(DeleteRequest(path))
ReportTaskCompletion → vm.answer(AnswerRequest(answer, refs))
```

All VM responses are protobuf, converted via `MessageToDict()` before serialization. ConnectErrors are caught and returned as tool results — the agent can observe and adapt.

### Claude-Specific: MCP Layer

For the `claude_cli` backend, tools are exposed via an MCP server (`agent_pipeline_claude/mcp_vm.py`). The 7 tools are registered with `@tool()` decorators. The final answer and grounding refs are stored in a mutable `vm_state` dict passed through the MCP server.

---

## Logging System

Every run writes to `runs/<YYYYMMDD>_runN/tasks/<task_id>/`:

```
tasks/t03/
├── meta.json              # task_id, model, task_fragment, started_at
├── api_calls.jsonl        # one line per VM call (cmd, args, result/error, ts)
├── react_trace.jsonl      # one line per ReAct step (state, plan, function, result_summary)
├── llm_parse_errors.jsonl # one line per LLM parse failure (attempt, error, raw_content)
└── result.json            # final_answer, verification_passed, step_count, finished_at
```

Global: `runs_log.jsonl` — benchmark truth table, one line per task run `{task_id, score, score_detail}`.

Mistake memory: `mistakes/<task_id>/errors.jsonl` — persists across runs, injected into the initial prompt as `[Past mistakes on this task]`.

---

## Claude-Specific Implementation Differences

| Feature | Base (`agent_pipeline/`) | Claude (`agent_pipeline_claude/`) |
|---|---|---|
| LLM client | `openai.OpenAI` | `anthropic.Anthropic` |
| Structured output | `beta.chat.completions.parse()` | `messages.parse()` |
| Tool protocol | OpenAI function calling | MCP server via `mcp_vm.py` |
| Models | GPT-4o etc. | claude-haiku-4-5, claude-sonnet-4-6 |
| Context/prompts | Shared (`agent_pipeline/prompts.py`) | Shared (`agent_pipeline/prompts.py`) |
| Models/data | Shared (`agent_pipeline/models.py`) | Shared (`agent_pipeline/models.py`) |

---

## Entry Points CLI Reference

```bash
# Run all tasks on Claude pipeline
./run_claude.sh

# Run specific tasks
./run_claude.sh t01 t03

# Specify model
./run_claude.sh --model claude-sonnet-4-6

# Run other backends
./run_openai.sh
./run_langchain.sh
./run_langgraph.sh

# Generic entry with full control
./run.sh --pipeline claude_cli --model claude-haiku-4-5 t01 t02
```

---

## Further Reading

- [API_SPEC.md](./API_SPEC.md) — Full REST API spec for `app.py` (harness + VM proxy)
- [docs/Pipeline.MD](./docs/Pipeline.MD) — Deep-dive pipeline documentation with full flow diagrams
