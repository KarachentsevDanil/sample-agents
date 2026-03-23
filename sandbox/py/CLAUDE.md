# Claude Code — Project Instructions

See [AGENTS.MD](./AGENTS.MD) for the full developer workflow, goal definition, and iteration process.

## Quick Rules

- **Propose first** — output a concise solution and plan BEFORE making any changes; get user confirmation
- **Self-verify** — after every change: build the project, run the pipeline, check scores
- **Never declare done without running** — `./run_claude.sh` must pass before the task is complete
- **Debug failing tasks individually** — see the debug workflow in AGENTS.MD
- **Iterate until 100%** — the definition of done is every task scoring 1.0

## Project Setup

```bash
# Install dependencies
uv sync

# Run the Claude pipeline
./run_claude.sh

# Run specific tasks only
./run_claude.sh t01 t03

# Run with a specific model
./run_claude.sh --model claude-sonnet-4-6
```

## Key Files

| File | Purpose |
|------|---------|
| `AGENTS.MD` | Developer workflow, goal, debug guide |
| `ARCHITECTURE.md` | Full technical architecture |
| `API_SPEC.md` | REST API reference for app.py |
| `docs/Pipeline.MD` | Deep-dive pipeline documentation |
| `agent_pipeline_claude/` | The pipeline we're improving |
| `agent_pipeline/prompts.py` | System prompts shared across pipelines |
