# Developer Guide — Agent Pipeline (Claude)

## Goal

Achieve **100% benchmark score** on `agent_pipeline_claude` — every task must return `score=1.0` from the harness.

## References

- Architecture: [ARCHITECTURE.md](./ARCHITECTURE.md)
- REST API spec: [API_SPEC.md](./API_SPEC.md)
- Pipeline deep dive: [docs/Pipeline.MD](./docs/Pipeline.MD)
- Quick start: [README.md](./README.md)

---

## Agent Behavior Rules

1. **Propose first** — Before writing any code, output a concise solution proposal and get user confirmation
2. **Clarify before executing** — If the approach is ambiguous, ask; don't guess
3. **Self-verify** — After every change: run the pipeline, check scores
4. **Iterate** — If any task scores < 1.0, diagnose and fix before declaring done
5. **Never skip verification** — The definition of "done" is all tasks scoring 1.0

---

## Improvement Workflow

### Step 1 — Isolated changes via git worktree

Work in an isolated worktree so the main branch stays clean for comparison:

```bash
# From the repo root
git worktree add ../claude-improve improve/claude-pipeline
cd ../claude-improve/sample-agents/sandbox/py
```

Make all changes in this worktree. The main branch remains runnable as a baseline.

### Step 2 — Run the full pipeline

```bash
./run_claude.sh
# or with a specific model:
./run_claude.sh --model claude-sonnet-4-6
```

Check output for per-task scores. Check `runs_log.jsonl` for the full benchmark truth table:

```bash
cat runs_log.jsonl | python3 -c "import sys,json; [print(l['task_id'], l['score']) for l in map(json.loads, sys.stdin)]"
```

### Step 3 — Debug specific failing tasks

Run a single task to isolate failures:

```bash
./run_claude.sh t03
```

Inspect the run logs (substitute `<run_dir>` with the latest `runs/` subdirectory):

```bash
# Final verdict and score
cat runs/<run_dir>/tasks/t03/result.json

# Step-by-step agent reasoning
cat runs/<run_dir>/tasks/t03/react_trace.jsonl

# Actual VM interactions (ground truth of what agent did)
cat runs/<run_dir>/tasks/t03/api_calls.jsonl

# LLM schema parse failures
cat runs/<run_dir>/tasks/t03/llm_parse_errors.jsonl

# Accumulated mistake memory for this task
cat mistakes/t03/errors.jsonl
```

### Step 4 — Diagnose root cause

Common failure patterns and where to look:

| Symptom | Likely cause | File to change |
|---|---|---|
| No answer after 30 steps | Agent loops or gets confused | `agent_pipeline_claude/react.py`, `agent_pipeline/prompts.py` SYSTEM_PROMPT |
| LLM parse errors in jsonl | Schema mismatch or model confusion | `agent_pipeline/models.py`, retry/hint logic in `react.py` |
| Wrong answer despite correct file reads | Verifier or system prompt issue | `agent_pipeline/prompts.py` VERIFIER_PROMPT |
| Tool call failures (`ConnectError`) | MCP wrapping issue | `agent_pipeline_claude/mcp_vm.py` |
| Context stage fails to suggest right files | File suggestion prompt too vague | `agent_pipeline/prompts.py` CONTEXT_SUGGESTION_PROMPT |
| Agent ignores AGENTS.md rules | System prompt not enforcing them | `agent_pipeline/prompts.py` SYSTEM_PROMPT |

### Step 5 — Make targeted changes

Prefer changes in this order (least risk first):

1. **Prompts** — `agent_pipeline/prompts.py`: SYSTEM_PROMPT, VERIFIER_PROMPT, CONTEXT_SUGGESTION_PROMPT, `build_initial_user_message()`
2. **ReAct loop logic** — `agent_pipeline_claude/react.py`: retry count, inline verify attempts, dispatch
3. **Context stage** — `agent_pipeline_claude/context.py`: pre-read file count, suggestion logic
4. **Verifier** — `agent_pipeline_claude/verifier.py`: pass/fail criteria
5. **MCP layer** — `agent_pipeline_claude/mcp_vm.py`: tool descriptions, parameter names, return formats

### Step 6 — Re-run and verify

```bash
# Re-run the failing task
./run_claude.sh t03

# Once that passes, run full suite
./run_claude.sh
```

Repeat steps 3–6 until all tasks score 1.0.

### Step 7 — Merge when 100%

```bash
# From main worktree
git merge improve/claude-pipeline
git worktree remove ../claude-improve
```

---

## Project Setup

This project is managed by `uv`.

```bash
# Install dependencies
uv sync

# Run default pipeline (agent_pipeline backend, OpenAI)
uv run python main.py

# Run Claude pipeline
./run_claude.sh
```
