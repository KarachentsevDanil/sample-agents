# Planning Stage Design

Goal: add a Claude-Code-style investigate → plan → track-execution flow to the pipeline.

```
ContextBuilderStage  →  PlanningStage  →  ReActLoopStage  →  VerifierStage
   (loads rules)         (creates plan)    (executes + tracks)
```

---

## Phase 0: What Exists Today

### `ContextBuilderStage` (context.py)
- Reads `AGENTS.md` ✓
- Gets DFS filesystem tree ✓
- LLM call: suggests up to 8 files relevant to the task ✓
- Pre-reads those files ✓
- Loads past mistakes ✓

**Gap**: agents.md is passed as a raw string. If agents.md says "see `99_process/process_tasks.md` for formatting rules", those files are never guaranteed to be loaded — they only get loaded if the LLM file suggester happens to pick them.

### `NextStep` model (models.py — OpenAI pipeline only)
```python
class NextStep(BaseModel):
    current_state: str
    plan_remaining_steps_brief: List[str]   # max 5 items, per-turn self-tracking
    task_completed: bool
    function: Union[ReportTaskCompletion, ...]
```

**Gap**: this is per-turn self-tracking, not an upfront plan. The agent generates it fresh each turn from scratch — no continuity. And the Claude pipeline has none of this at all.

### `PipelineContext` (models.py)
```python
@dataclass
class PipelineContext:
    task: str
    agents_md: str = ""
    dfs_tree: str = ""
    preread_files: dict = {}
    past_mistakes: list = []
    react_trace: list = []       # tool steps
    files_used: list = []
    final_answer: str = ""
    ...
```

**Gap**: no `task_plan`, no plan progress tracking.

---

## Phase 1: Enhance `ContextBuilderStage` — Recursive Rules Loading

### The problem
`agents.md` often contains instructions like:
- `"Follow the formatting rules in 99_process/document_capture.md"`
- `"See 02_distill/AGENTS.md for card format"`
- `"Before any task, read 90_memory/Soul.md"`

Today: the LLM file suggester may or may not pick these up depending on the task phrasing.
Today: `MANDATORY_PREREAD_PATTERNS` is hardcoded — not derived from agents.md content.

### The fix: parse agents.md for explicit file references

Add a second pass after reading agents.md:

```python
class RulesExtraction(BaseModel):
    referenced_files: List[str] = Field(
        description="All file paths explicitly mentioned in agents.md that contain rules, "
                    "templates, or process instructions the agent must follow"
    )
    key_rules: List[str] = Field(
        description="The 3-7 most important rules extracted verbatim or paraphrased from agents.md"
    )

class ContextBuilderStage:
    def execute(self, ctx, logger):
        ctx.agents_md_path, ctx.agents_md = self._fetch_agents_md()
        ctx.dfs_tree = self._fetch_dfs()

        # NEW: extract referenced files and key rules from agents.md
        rules = self._extract_rules(ctx.agents_md, ctx.task, logger)
        ctx.rules_files = rules.referenced_files     # NEW field
        ctx.key_rules = rules.key_rules              # NEW field

        # Pre-read: referenced rule files take priority, then LLM suggestions
        rule_files = self._filter_existing(rules.referenced_files, ctx.dfs_tree)
        suggested = self._suggest_files(ctx, logger)
        all_paths = list(dict.fromkeys(rule_files + suggested))[:MAX_PREREAD_FILES]
        ctx.preread_files = self._read_files(all_paths)
        ctx.past_mistakes = logger.load_past_mistakes()
```

Key: `_extract_rules()` is a cheap LLM call on agents.md content (not the full task context). It finds what agents.md *itself* says to read. This is deterministic per agents.md content — could be cached.

---

## Phase 2: New `PlanningStage`

### Data models

```python
# In models.py

class PlanStep(BaseModel):
    id: str                    # "1", "2", "3"
    description: str           # "Read Soul.md to understand current state"
    rationale: str             # "Soul.md contains the memory structure we'll modify"
    expected_tools: List[str]  # ["read"] — helps validator later

class TaskPlan(BaseModel):
    task_interpretation: str   # one-sentence restatement of the task
    relevant_rules: List[str]  # rules from agents.md that apply to THIS task
    steps: List[PlanStep]      # the actual plan
    complexity: Literal["simple", "medium", "complex"]
    max_steps_estimate: int    # estimated steps needed in ReAct loop
```

### Plan size control

```python
PLAN_SIZE_CONFIG = {
    "simple":  {"min": 1, "max": 3,  "react_max_steps": 10},
    "medium":  {"min": 3, "max": 6,  "react_max_steps": 20},
    "complex": {"min": 5, "max": 10, "react_max_steps": 30},
}
```

The planner LLM determines complexity. The config maps complexity → plan depth + ReAct budget. This is the "play with plan size" knob — adjust the config to experiment.

### `PlanningStage` implementation

```python
class PlanningStage:
    PLANNING_SYSTEM = """
You are a task planner. Given a task description and loaded context (rules, filesystem, file contents),
create a precise execution plan.

Rules:
- Be specific — each step should reference actual files/directories when known
- Be honest about complexity — don't simplify complex tasks
- Apply ONLY the rules from agents.md that are relevant to this specific task
- Steps should be sequential with no branching (branching is handled by the ReAct loop)
- If you don't know something, create an exploration step (e.g., "read X to understand Y")
"""

    def execute(self, ctx: PipelineContext, logger) -> None:
        user_content = self._build_planning_prompt(ctx)

        resp = self._client.messages.parse(
            model=self._model,
            max_tokens=2048,
            system=self.PLANNING_SYSTEM,
            messages=[{"role": "user", "content": user_content}],
            output_format=TaskPlan,
        )
        logger.append_api_call(build_api_log_entry("planning", ...))

        ctx.task_plan = resp.parsed_output
        ctx.plan_progress = [{"step_id": s.id, "done": False} for s in ctx.task_plan.steps]

        # Override MAX_STEPS based on plan complexity
        config = PLAN_SIZE_CONFIG[ctx.task_plan.complexity]
        ctx.react_max_steps = config["react_max_steps"]

        # Print plan for observability
        print(f"\n[PLAN] {ctx.task_plan.task_interpretation}")
        print(f"[PLAN] Complexity: {ctx.task_plan.complexity} ({len(ctx.task_plan.steps)} steps)")
        for step in ctx.task_plan.steps:
            print(f"  {step.id}. {step.description}")

    def _build_planning_prompt(self, ctx: PipelineContext) -> str:
        parts = [f"Task: {ctx.task}\n"]

        if ctx.key_rules:
            parts.append("Rules from AGENTS.md:\n" + "\n".join(f"- {r}" for r in ctx.key_rules))

        if ctx.preread_files:
            parts.append("\nLoaded files:")
            for path, content in ctx.preread_files.items():
                parts.append(f"\n--- {path} ---\n{content[:800]}")  # truncated

        parts.append(f"\nFilesystem structure:\n{ctx.dfs_tree[:1000]}")

        return "\n".join(parts)
```

---

## Phase 3: Plan Tracking in `ReActLoopStage`

### Two-level tracking

**Level 1 — Agent self-tracking** (already exists in OpenAI pipeline via `NextStep`):
- Agent sees the plan in its initial message
- Agent updates `plan_remaining_steps_brief` each turn from the plan
- This gives the agent continuity: it always knows where it is

**Level 2 — External tracking** (new, in `PipelineContext`):
- After each `NextStep` response, compare `plan_remaining_steps_brief` to `ctx.task_plan.steps`
- When a step disappears from remaining_work, mark it as done in `ctx.plan_progress`
- Log plan progress events separately

### Changes to `ReActLoopStage`

**Inject plan into initial message:**

```python
def _build_initial_message(self, ctx: PipelineContext) -> str:
    base = build_initial_user_message(ctx.task, ctx.agents_md, ...)

    if ctx.task_plan:
        plan_block = "\n\n## Your Execution Plan\n"
        plan_block += f"Complexity: {ctx.task_plan.complexity}\n\n"
        for step in ctx.task_plan.steps:
            plan_block += f"{step.id}. {step.description}\n"
        plan_block += "\nWork through these steps in order. Update your remaining steps as you go."
        return base + plan_block

    return base
```

**Track plan progress after each structured output** (OpenAI pipeline):

```python
def _update_plan_progress(self, ctx: PipelineContext, next_step: NextStep) -> None:
    if not ctx.task_plan:
        return

    remaining = set(next_step.plan_remaining_steps_brief)
    for entry in ctx.plan_progress:
        if not entry["done"]:
            step = next((s for s in ctx.task_plan.steps if s.id == entry["step_id"]), None)
            if step and step.description not in remaining:
                entry["done"] = True
                entry["completed_at_react_step"] = len(ctx.react_trace)

# Call after each NextStep parse:
self._update_plan_progress(ctx, next_step)
```

**Use dynamic `react_max_steps`:**

```python
max_steps = getattr(ctx, "react_max_steps", MAX_STEPS)
while step_count < max_steps:
    ...
```

---

## Phase 4: PipelineContext Changes

```python
@dataclass
class PipelineContext:
    task: str
    model: str
    agents_md: str = ""
    agents_md_path: str = ""
    dfs_tree: str = ""
    preread_files: dict = field(default_factory=dict)
    past_mistakes: list = field(default_factory=list)

    # NEW — from enhanced ContextBuilderStage
    rules_files: list = field(default_factory=list)   # files referenced in agents.md
    key_rules: list = field(default_factory=list)      # extracted rules

    # NEW — from PlanningStage
    task_plan: object = None                           # TaskPlan | None
    plan_progress: list = field(default_factory=list)  # [{step_id, done, completed_at}]
    react_max_steps: int = 30                          # overridden by plan complexity

    # existing
    react_trace: list = field(default_factory=list)
    files_used: list = field(default_factory=list)
    final_answer: str = ""
    final_code: str = ""
    verification_passed: bool = False
    verification_reason: str = ""
```

---

## Phase 5: Pipeline Composition

```python
# In agent.py or wherever Pipeline is constructed:

pipeline = (
    Pipeline(model=model, vm=vm, task=task, task_id=task_id, run_dir=run_dir)
    .use_context()    # loads agents.md + follows references + suggests files
    .use_planning()   # creates explicit plan, sets react budget
    .use_react()      # executes with plan in context, tracks progress
    .use_response_verifier()
)
```

`use_planning()` is opt-in — existing `.use_context().use_react()` flows still work unchanged.

---

## "Play with Plan Size" — The Knobs

Three dimensions to experiment with:

### 1. Plan granularity (steps count)
```python
PLAN_SIZE_CONFIG = {
    "simple":  {"min_steps": 1, "max_steps": 3,  "react_budget": 10},
    "medium":  {"min_steps": 3, "max_steps": 6,  "react_budget": 20},
    "complex": {"min_steps": 5, "max_steps": 10, "react_budget": 30},
}
```
Change `max_steps` per complexity tier to control how detailed plans get.

### 2. Plan update frequency
- **Sticky plan** (default): initial plan is fixed, agent only checks off steps
- **Replanning**: allow agent to update plan mid-execution if it discovers new info
  - Triggered when `current_state` contains "unexpected" or "plan needs revision"
  - Agent proposes new plan → `PlanningStage.replan()` → inject updated plan

### 3. Plan vs. no-plan A/B
Because `use_planning()` is opt-in, you can run both modes against the same task batch and compare:
- Step count distribution
- Token cost
- Success rate
- Time to completion

Track in `result.json` whether planning was used: `"planning_enabled": true`.

---

## What Gets Logged

```json
// meta.json (extended)
{
  "task_id": "t01",
  "model": "claude-sonnet-4-6",
  "planning_enabled": true,
  "plan_complexity": "medium",
  "plan_steps_count": 5,
  "react_max_steps": 20,
  "rules_files_loaded": ["99_process/process_tasks.md", "02_distill/AGENTS.md"],
  "key_rules_count": 6
}

// plan.json (new file, written by PlanningStage)
{
  "task_interpretation": "Remove all captured cards from 02_distill/cards/",
  "relevant_rules": ["Cards must be deleted, not moved", "Update Soul.md after deletion"],
  "complexity": "simple",
  "steps": [
    {"id": "1", "description": "Read Soul.md to understand current card state", "expected_tools": ["read"]},
    {"id": "2", "description": "List 02_distill/cards/ to find all captured cards", "expected_tools": ["list", "find"]},
    {"id": "3", "description": "Delete each captured card file", "expected_tools": ["delete"]}
  ]
}

// plan_progress.json (new file, written at end)
[
  {"step_id": "1", "done": true,  "completed_at_react_step": 2},
  {"step_id": "2", "done": true,  "completed_at_react_step": 4},
  {"step_id": "3", "done": true,  "completed_at_react_step": 9}
]
```

---

## Implementation Order

| Step | What | Files changed | Effort |
|------|------|---------------|--------|
| 1 | Add `RulesExtraction` model + `_extract_rules()` to ContextBuilderStage | `context.py`, `models.py` | 1h |
| 2 | Add `task_plan`, `plan_progress`, `react_max_steps` to `PipelineContext` | `models.py` | 30min |
| 3 | Create `PlanningStage` with `TaskPlan` model | new `planning.py`, `models.py` | 2h |
| 4 | Add plan injection to initial message in `ReActLoopStage` | `react.py` (both) | 30min |
| 5 | Add `_update_plan_progress()` to OpenAI react loop | `agent_pipeline_openai/react.py` | 1h |
| 6 | Add `use_planning()` to `Pipeline` | `pipeline.py` | 15min |
| 7 | Write `plan.json` and `plan_progress.json` in `RunLogger` | `logger.py` | 30min |
| **Total** | | | **~6h** |

---

## Open Questions Before Implementation

1. **Claude pipeline structured output**: the Claude pipeline uses native tool_use (no `NextStep`). Should we add structured output to Claude too, or only track plan progress in the OpenAI pipeline first? — Recommendation: OpenAI pipeline first, port to Claude second.

2. **Replanning**: allow mid-execution plan revision? — Start with sticky plan (simpler). Add replanning once baseline metrics are collected.

3. **Rules extraction caching**: `_extract_rules()` is called per task but agents.md rarely changes. Cache by `sha256(agents_md_content)` to avoid redundant LLM calls across tasks in the same run.

4. **Plan size experimentation**: what's the right metric? Track `plan_steps_count` vs. `react_trace` length vs. `final_code == OUTCOME_OK`. If planned tasks consistently use fewer steps than unplanned, planning is working.
