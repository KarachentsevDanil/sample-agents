from dataclasses import dataclass
from typing import Annotated, Any, TypedDict

from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    task: str
    model: str
    agents_md: str
    agents_md_path: str
    dfs_tree: str
    preread_files: dict
    past_mistakes: list
    react_trace: list
    files_used: list
    final_answer: str
    final_code: str
    verification_passed: bool
    verification_reason: str
    step_count: int
    inline_verify_attempts: int


@dataclass
class GraphContext:
    """Immutable run-scoped dependencies injected via runtime.context."""
    vm: Any          # MiniRuntimeClientSync
    client: Any      # openai.OpenAI
    logger: Any      # agent_pipeline.logger.RunLogger
    task_id: str = ""
