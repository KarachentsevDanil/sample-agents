import hashlib
from dataclasses import dataclass, field
from typing import Annotated, Any, List, Literal

from annotated_types import Ge, Le, MaxLen, MinLen
from pydantic import BaseModel, Field


class ReportTaskCompletion(BaseModel):
    tool: Literal["report_completion"] = "report_completion"
    completed_steps_laconic: List[str]
    answer: str
    grounding_refs: List[str] = Field(default_factory=list)
    code: Literal["completed", "failed"]
    reason: str = ""


class ToolResultEnvelope(BaseModel):
    current_state: str
    plan_remaining_steps_brief: Annotated[List[str], MinLen(1), MaxLen(5)] = Field(
        default_factory=lambda: ["Proceed with the next relevant step."]
    )
    result_summary: str


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
