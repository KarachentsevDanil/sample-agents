# Full Picture — Pipeline Evolution

Analysis date: 2026-03-25
Source: ERC3 1st-place implementation + current codebase audit

---

## Where We Are Today

```
Task  →  ContextBuilderStage  →  ReActLoopStage  →  VerifierStage  →  Result
```

| Stage | What it does | Gaps |
|-------|-------------|------|
| **ContextBuilderStage** | Reads AGENTS.md, gets DFS tree, LLM-suggests up to 8 files, pre-reads them, loads past mistakes | agents.md treated as flat string — referenced rule files not guaranteed to load; MANDATORY_PREREAD_PATTERNS hardcoded |
| **ReActLoopStage** (Claude) | Native tool_use loop, max 30 steps, step budget warning at step 27 | No structured output — no `current_state`, no plan, no self-tracking |
| **ReActLoopStage** (OpenAI) | `NextStep` structured output with `plan_remaining_steps_brief` (max 5), per-turn self-tracking | Plan is regenerated fresh each turn — no upfront plan, no continuity, not tracked externally |
| **VerifierStage** | Post-execution LLM verification of final answer | Runs after completion only |

**Core problem**: the agent enters the ReAct loop without an explicit plan. It reasons step-by-step but has no upfront model of what success looks like. `remaining_work` is regenerated from scratch each turn — no memory of what was planned.

---

## Where We Want to Go

```
Task
  ↓
ContextBuilderStage (enhanced)
  → reads AGENTS.md
  → LLM extracts: referenced rule files + key rules
  → loads rule files (deterministic) + task-relevant files (LLM-suggested)
  ↓
PlanningStage (new)
  → LLM produces: TaskPlan {steps, complexity, relevant_rules}
  → sets react budget based on complexity
  → writes plan.json
  ↓
ReActLoopStage (enhanced)
  → injects plan into initial message
  → agent self-tracks via remaining_work
  → external tracker marks steps done
  → writes plan_progress.json
  ↓
VerifierStage
  ↓
Result
```

---

## The Three Improvements

### 1. Dynamic Rules Loading (enhancement to existing stage)

**Current**: agents.md is a raw string. The LLM file suggester may or may not pick up files that agents.md explicitly references.

**New**: a dedicated `_extract_rules()` LLM call on agents.md content that finds:
- `referenced_files` — all files agents.md says to read before any task
- `key_rules` — 3-7 most important rules, verbatim

Referenced files load first, before LLM-suggested files. This ensures rule files are never missed.

**Why it matters**: if agents.md says "before writing any card, read `02_distill/cards/_card-template.md`", that file must always be loaded — not only when the LLM file suggester happens to pick it.

**"Play with context"**: `_extract_rules()` result can be cached by `sha256(agents_md)` — one LLM call per unique agents.md content across the whole run.

---

### 2. Planning Stage (new stage)

**A dedicated LLM call that produces a structured plan before the ReAct loop starts.**

```
Input:  task + key_rules + preread file contents + DFS tree
Output: TaskPlan { task_interpretation, relevant_rules, steps[], complexity }
```

Each `PlanStep` has: `id`, `description`, `rationale`, `expected_tools`.

**"Play with plan size"** — three levers:

| Complexity | Plan steps | ReAct budget |
|-----------|-----------|-------------|
| simple | 1–3 | 10 |
| medium | 3–6 | 20 |
| complex | 5–10 | 30 |

The planner LLM determines complexity. The config maps it to plan depth + step budget. Adjust the config table to experiment — simple tasks that get over-planned waste tokens; complex tasks that under-plan run out of steps.

**Why upfront planning helps**:
- Agent enters the loop with a model of success, not just the task description
- Reduces "flailing" (exploring without direction in early steps)
- Makes remaining_work meaningful — agent updates a real plan, not invents one each turn
- Enables external progress tracking

---

### 3. Plan Tracking During Execution

**Two levels:**

**Agent self-tracking** (already exists in OpenAI pipeline via `NextStep.plan_remaining_steps_brief`):
- Agent sees the plan in its initial message
- Updates `remaining_work` as it completes steps
- Has continuity — doesn't regenerate the plan from scratch each turn

**External tracking** (new in `PipelineContext`):
- `ctx.task_plan` — the full plan from PlanningStage
- `ctx.plan_progress` — `[{step_id, done, completed_at_react_step}]`
- After each `NextStep`, compare agent's `remaining_work` against plan steps → mark done
- Written to `plan_progress.json` at end of run

**Like Claude Code's task list**: you see the plan upfront, items get checked off as work progresses, the final state shows what was done vs. what was planned.

---

## ERC3 Diagram → Our Mapping

```
ERC3                              Ours
─────────────────────────────── → ───────────────────────────────────────
One-Time Ingestion                (no benchmark prep phase; agents.md
  Preparation Script              varies per task VM — rules loaded
  → Cached Rules (Public/Auth)    dynamically per task)
  → Formatting Instructions
  [1.4X badge = key gain]

Whoami & Context Prep          →  ContextBuilderStage._fetch_agents_md()
                                  + recursive rules extraction (new)

LLM Context Selector &         →  ContextBuilderStage._suggest_files()
  System Prompt Builder            + PlanningStage.relevant_rules injection

AGENT (Plan ReAct)             →  ReActLoopStage
  Inputs: History, Rules, Tools    Input: plan + rules + preread files + tools

Structured Output              →  NextStep (OpenAI pipeline) ✓
  current_state                    current_state ✓
  remaining_work                   plan_remaining_steps_brief ✓ (max 5)
  next_action                      function ✓
  function                         task_completed ✓

Function is /respond?          →  tool_name == "report_completion" check ✓

Step Validator (Gatekeeper)    →  NOT IMPLEMENTED — see evolve-pipelines.md
  Rejected → Ephemeral Feedback    (planned as ActionValidator)
  Approved → Execute Tool

Execute Tool                   →  _dispatch_tool() / @function_tool wrappers ✓
  SDK w/ Pagination               (pagination not implemented — reads are full)
  Error Handling                  ConnectError handling ✓

Compress & Log to History      →  NOT IMPLEMENTED
  Action Summary +                 (react_trace stores raw; no compression)
  Tool Call + Result               see evolve-pipelines.md Best Practice 3

Update History                 →  messages.append() ✓
  Successful Steps Only            (all steps logged — no filtering)
  Latest Plan                      (no plan persistence between turns)
```

---

## Files

| File | What it contains |
|------|-----------------|
| `docs/evolve-pipelines.md` | 6 best practices from ERC3, ranked by impact, with code sketches |
| `docs/logging-investigation.md` | ERC3 visualizer reverse-engineering, gap analysis, 3-phase replication plan |
| `docs/planning-design.md` | Detailed design spec for ContextBuilder enhancement + PlanningStage + plan tracking |
| `docs/full-picture.md` | This file — end-to-end view |

---

## Priority Order

| Priority | What | Effort | Value |
|----------|------|--------|-------|
| 1 | **Recursive rules loading** in ContextBuilderStage | 1h | High — rules always loaded correctly |
| 2 | **PlanningStage** + `TaskPlan` model | 2–3h | High — upfront plan, dynamic budget |
| 3 | **Plan injection** into ReAct initial message | 30min | High — agent sees plan from step 1 |
| 4 | **External plan tracking** in PipelineContext | 1h | Medium — observability + logging |
| 5 | **Step validator** (ActionValidator) | 1–2h | Medium — token savings, audit trail |
| 6 | **Conversation compression** per step | 2h | Medium — context hygiene |
| 7 | **Trace visualization** (logging-investigation.md) | 10–12h | Medium — debugging tooling |

Items 1–4 are the core ask: investigate → plan → track. Items 5–7 are pipeline quality improvements from ERC3.

---

## Open Questions

1. **Claude pipeline structured output**: currently no `NextStep` equivalent. Add it in parallel with planning, or use planning to drive Claude via initial message only and skip external step-level tracking for now?

2. **Replanning**: if agent discovers mid-execution that the plan is wrong, allow updating `ctx.task_plan`? Start with sticky plan; add replanning once baseline is measured.

3. **Plan size experiment**: what metric proves planning helps? Recommended: track `plan_steps_count` vs. `react_trace` length vs. `final_code`. Run same task batch with and without `.use_planning()` and compare.

4. **Rules caching**: cache `_extract_rules()` result by `sha256(agents_md)` — avoids repeated LLM call across tasks in the same benchmark run that share the same agents.md.
