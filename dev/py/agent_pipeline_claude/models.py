from dataclasses import dataclass, field
from typing import Any, List, Literal, Optional

from pydantic import BaseModel, Field


# ── Planning (T3: planning stage) ───────────────────────────────────

class PlanStep(BaseModel):
    id: str
    description: str
    rationale: str
    expected_tools: List[str] = Field(default_factory=list)


class TaskPlan(BaseModel):
    task_interpretation: str
    relevant_rules: List[str] = Field(default_factory=list)
    steps: List[PlanStep]
    complexity: Literal["simple", "medium", "complex"]
    max_steps_estimate: int
    early_outcome: Optional[Literal[
        "OUTCOME_NONE_UNSUPPORTED",
        "OUTCOME_NONE_CLARIFICATION",
        "OUTCOME_DENIED_SECURITY",
    ]] = None
    early_outcome_reason: Optional[str] = None


PLAN_SIZE_CONFIG = {
    "simple":  {"min_steps": 1, "max_steps": 3,  "react_max_steps": 8},
    "medium":  {"min_steps": 3, "max_steps": 6,  "react_max_steps": 14},
    "complex": {"min_steps": 5, "max_steps": 8,  "react_max_steps": 18},
}


# Budget from plan length: plan_steps * 2.5 + 3, capped at 20
def budget_from_plan(plan_steps: int) -> int:
    return min(int(plan_steps * 2.5 + 3), 20)


@dataclass
class PipelineContext:
    task: str
    model: str
    agents_md: str = ""
    agents_md_path: str = ""
    dfs_tree: str = ""
    preloaded_context_files: dict[str, str] = field(default_factory=dict)
    past_mistakes: list = field(default_factory=list)
    react_trace: list = field(default_factory=list)
    files_used: list = field(default_factory=list)
    final_answer: str = ""
    final_code: str = ""
    vm_time: str = ""
    injection_risk_notes: str = ""

    # T3: planning stage
    task_plan: Optional["TaskPlan"] = None
    plan_progress: list = field(default_factory=list)
    react_max_steps: int = 18

    # T5: action validator
    validation_log: list = field(default_factory=list)

    # Pipeline control
    pipeline_complete: bool = False
    harness_answer_submitted: bool = False

    # Observability
    loop_termination_reason: str = ""


@dataclass
class AgentRuntimeContext:
    vm: Any
    pipeline: PipelineContext
    logger: Any
    model: str
    step_idx: int = 0
    last_step_ts: float = 0.0
