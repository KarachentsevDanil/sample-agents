import hashlib
from dataclasses import dataclass, field
from typing import Any, List, Literal, Optional

from pydantic import BaseModel, Field


class ReportTaskCompletion(BaseModel):
    tool: Literal["report_completion"]
    completed_steps_laconic: List[str]
    message: str
    grounding_refs: List[str] = Field(default_factory=list)
    outcome: Literal[
        "OUTCOME_OK",
        "OUTCOME_DENIED_SECURITY",
        "OUTCOME_NONE_CLARIFICATION",
        "OUTCOME_NONE_UNSUPPORTED",
        "OUTCOME_ERR_INTERNAL",
    ]


# ── Trusted rule graph (context building) ───────────────────────────

class RulesExtraction(BaseModel):
    referenced_files: List[str] = Field(
        default_factory=list,
        description="Trusted rule-graph paths discovered from AGENTS.md references.",
    )
    key_rules: List[str] = Field(
        default_factory=list,
        description="Short summaries of the most relevant trusted rules.",
    )


class FileSuggestion(BaseModel):
    files_to_read: List[str] = Field(
        default_factory=list,
        description="Trusted rule files to preload before the ReAct loop.",
    )

@dataclass
class RuleGraphNode:
    """One trusted node discovered from the root AGENTS.md dependency graph."""
    path: str
    content: str
    refs: List[str]
    depth: int
    parent_path: Optional[str] = None
    source_type: Literal["root_agents", "agents_child", "rule_doc", "template", "other"] = "other"
    trust_level: Literal["trusted_rule_graph", "untrusted_data"] = "trusted_rule_graph"
    is_instruction_candidate: bool = True
    resolution_reasons: List[str] = field(default_factory=list)


@dataclass
class RuleGraph:
    """BFS-traversed graph of all trusted rule files reachable from root AGENTS.md."""
    root_path: str
    nodes: dict[str, RuleGraphNode]
    edges: dict[str, List[str]]
    ordered_paths_bfs: List[str]
    mandatory_paths: List[str] = field(default_factory=list)
    task_relevant_paths: List[str] = field(default_factory=list)
    max_depth_reached: int = 0
    truncated_by_limits: bool = False
    cycles_detected: bool = False


class RuleGraphAssessment(BaseModel):
    mandatory_paths: List[str] = Field(
        default_factory=list,
        description="Trusted rule-graph paths that must be loaded before execution for this task.",
    )
    task_relevant_rule_paths: List[str] = Field(
        default_factory=list,
        description="Trusted rule-graph paths most relevant to the task, including mandatory ones.",
    )
    relevant_rule_summaries: List[str] = Field(
        default_factory=list,
        description="Concise summaries of the trusted rules that matter for this task.",
    )
    optional_task_data_hints: List[str] = Field(
        default_factory=list,
        description="Filesystem paths that may be useful task data, but are not authoritative rules.",
    )
    injection_risk_notes: List[str] = Field(
        default_factory=list,
        description="Short notes about likely prompt-injection boundaries or authority conflicts.",
    )


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


PLAN_SIZE_CONFIG = {
    "simple":  {"min_steps": 1, "max_steps": 3,  "react_max_steps": 10},
    "medium":  {"min_steps": 3, "max_steps": 6,  "react_max_steps": 20},
    "complex": {"min_steps": 5, "max_steps": 10, "react_max_steps": 30},
}


class VerificationResult(BaseModel):
    passed: bool
    reason: str


@dataclass
class PipelineContext:
    task: str
    model: str
    agents_md: str = ""
    agents_md_path: str = ""
    dfs_tree: str = ""
    preread_files: dict[str, str] = field(default_factory=dict)
    past_mistakes: list = field(default_factory=list)
    react_trace: list = field(default_factory=list)
    files_used: list = field(default_factory=list)
    final_answer: str = ""
    final_code: str = ""
    verification_passed: bool = False
    verification_reason: str = ""

    # Context: trusted rule graph rooted at AGENTS.md
    rule_graph: Optional["RuleGraph"] = None
    rule_graph_summary: str = ""
    preloaded_context_files: dict[str, str] = field(default_factory=dict)
    key_rules: list[str] = field(default_factory=list)
    rules_files: list[str] = field(default_factory=list)
    mandatory_rule_paths: list[str] = field(default_factory=list)
    task_relevant_rule_summaries: list[str] = field(default_factory=list)
    untrusted_task_file_hints: list[str] = field(default_factory=list)
    rule_graph_injection_risk_notes: list[str] = field(default_factory=list)
    context_blocks: list = field(default_factory=list)

    # T3: planning stage
    task_plan: Optional["TaskPlan"] = None
    plan_progress: list = field(default_factory=list)
    react_max_steps: int = 30

    # T5: action validator
    validation_log: list = field(default_factory=list)

    # Pipeline control
    pipeline_complete: bool = False   # Set by CapabilityCheckStage to skip remaining stages

    # Observability
    loop_termination_reason: str = ""   # "report_completion" | "max_steps_reached" | "exception" | "capability_blocked" | "retry"
    capability_blocked: bool = False    # True when CapabilityCheckStage short-circuited
    retry_count: int = 0                # Number of ReAct retries attempted

    @property
    def task_hash(self) -> str:
        return hashlib.sha256(self.task.encode()).hexdigest()[:16]


@dataclass
class AgentRuntimeContext:
    vm: Any
    pipeline: PipelineContext
    logger: Any
    model: str
    step_idx: int = 0
    last_step_ts: float = 0.0   # Timestamp of the previous step (for duration tracking)
