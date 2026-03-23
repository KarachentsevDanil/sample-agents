from dataclasses import dataclass
from typing import Any

from agent_pipeline.models import FileSuggestion, PipelineContext, ReportTaskCompletion, VerificationResult


@dataclass
class AgentRuntimeContext:
    vm: Any
    pipeline: PipelineContext
    logger: Any
    model: str
    step_idx: int = 0
