import hashlib
from dataclasses import dataclass, field
from typing import Annotated, List, Literal, Union

from annotated_types import Ge, Le, MaxLen, MinLen
from pydantic import BaseModel, Field


class ReportTaskCompletion(BaseModel):
    tool: Literal["report_completion"]
    completed_steps_laconic: List[str]
    answer: str
    grounding_refs: List[str] = Field(default_factory=list)
    code: Literal["completed", "failed"]


class Req_Tree(BaseModel):
    tool: Literal["tree"]
    path: str = Field(..., description="folder path")


class Req_Search(BaseModel):
    tool: Literal["search"]
    pattern: str
    count: Annotated[int, Ge(1), Le(10)] = 5
    path: str = "/"


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
        Req_Search,
        Req_List,
        Req_Read,
        Req_Write,
        Req_Delete,
    ] = Field(..., description="execute first remaining step")


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
