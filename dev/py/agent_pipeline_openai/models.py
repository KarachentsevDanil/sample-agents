import hashlib
from dataclasses import dataclass, field
from typing import Any, List, Literal

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
