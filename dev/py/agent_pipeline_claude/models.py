import hashlib
from dataclasses import dataclass, field
from typing import Annotated, List, Literal, Optional, Union

from annotated_types import Ge, Le, MaxLen, MinLen
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


class Req_Tree(BaseModel):
    tool: Literal["tree"]
    root: str = Field("", description="tree root, empty means repository root")


class Req_Find(BaseModel):
    tool: Literal["find"]
    name: str
    root: str = "/"
    kind: Literal["all", "files", "dirs"] = "all"
    limit: Annotated[int, Ge(1), Le(20)] = 10


class Req_Search(BaseModel):
    tool: Literal["search"]
    pattern: str
    limit: Annotated[int, Ge(1), Le(20)] = 10
    root: str = "/"


class Req_List(BaseModel):
    tool: Literal["list"]
    path: str


class Req_Read(BaseModel):
    tool: Literal["read"]
    path: str


class Req_Write(BaseModel):
    tool: Literal["write"]
    path: str
    content: str


class Req_Delete(BaseModel):
    tool: Literal["delete"]
    path: str


class Req_MkDir(BaseModel):
    tool: Literal["mkdir"]
    path: str


class Req_Move(BaseModel):
    tool: Literal["move"]
    from_name: str
    to_name: str


class NextStep(BaseModel):
    current_state: str
    plan_remaining_steps_brief: Annotated[List[str], MinLen(1), MaxLen(5)] = Field(
        ...,
        description="explain your thoughts on how to accomplish - what steps to execute",
    )
    task_completed: bool
    function: Union[
        ReportTaskCompletion,
        Req_Tree,
        Req_Find,
        Req_Search,
        Req_List,
        Req_Read,
        Req_Write,
        Req_Delete,
        Req_MkDir,
        Req_Move,
    ] = Field(..., description="execute first remaining step")


# ── Rules extraction (T2: context enhancement) ──────────────────────

class RulesExtraction(BaseModel):
    referenced_files: List[str] = Field(
        default_factory=list,
        description="File paths explicitly mentioned in agents.md that contain rules or templates",
    )
    key_rules: List[str] = Field(
        default_factory=list,
        description="3-7 most important rules extracted verbatim from agents.md",
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


class FileSuggestion(BaseModel):
    files_to_read: List[str] = Field(
        default_factory=list,
        description="Absolute paths to pre-read before the ReAct loop, max 8",
    )


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
    preread_files: dict = field(default_factory=dict)
    past_mistakes: list = field(default_factory=list)
    react_trace: list = field(default_factory=list)
    files_used: list = field(default_factory=list)
    final_answer: str = ""
    final_code: str = ""
    verification_passed: bool = False
    verification_reason: str = ""
    node_counter: int = 0

    # T2: rules extraction
    key_rules: list = field(default_factory=list)
    rules_files: list = field(default_factory=list)
    context_blocks: list = field(default_factory=list)  # Selected context blocks for this task

    # T3: planning stage
    task_plan: Optional["TaskPlan"] = None
    plan_progress: list = field(default_factory=list)
    react_max_steps: int = 30

    # T5: action validator
    validation_log: list = field(default_factory=list)

    @property
    def task_hash(self) -> str:
        return hashlib.sha256(self.task.encode()).hexdigest()[:16]
