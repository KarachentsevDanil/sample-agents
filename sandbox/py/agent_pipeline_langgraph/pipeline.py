import time

from langgraph.graph import END, START, StateGraph
from openai import OpenAI

from bitgn.vm.mini_connect import MiniRuntimeClientSync

from agent_pipeline.logger import RunLogger
from agent_pipeline.models import PipelineContext

from .context import context_builder_node
from .react import MAX_STEPS, react_agent_node, react_tools_node
from .state import AgentState, GraphContext
from .verifier import verifier_node
from openai_config import OPENAI_API_KEY


def route_after_agent(state: AgentState) -> str:
    last = state["messages"][-1]
    if hasattr(last, "tool_calls") and last.tool_calls:
        return "react_tools"
    return "verifier"


def route_after_tools(state: AgentState) -> str:
    if state.get("final_answer"):
        return "verifier"
    if state.get("step_count", 0) >= MAX_STEPS:
        return "verifier"
    return "react_agent"


def _build_graph():
    builder = StateGraph(state_schema=AgentState, context_schema=GraphContext)

    builder.add_node("context_builder", context_builder_node)
    builder.add_node("react_agent", react_agent_node)
    builder.add_node("react_tools", react_tools_node)
    builder.add_node("verifier", verifier_node)

    builder.add_edge(START, "context_builder")
    builder.add_edge("context_builder", "react_agent")
    builder.add_conditional_edges(
        "react_agent",
        route_after_agent,
        {"react_tools": "react_tools", "verifier": "verifier"},
    )
    builder.add_conditional_edges(
        "react_tools",
        route_after_tools,
        {"react_agent": "react_agent", "verifier": "verifier"},
    )
    builder.add_edge("verifier", END)

    return builder.compile()


_GRAPH = _build_graph()


def run_graph(
    model: str,
    vm: MiniRuntimeClientSync,
    task_text: str,
    task_id: str = "",
    run_dir=None,
) -> PipelineContext:
    client = OpenAI(api_key=OPENAI_API_KEY)
    logger = RunLogger(task_id=task_id, run_dir=run_dir)
    logger.write_meta({
        "task_id": task_id,
        "model": model,
        "pipeline": "langgraph",
        "ts": time.time(),
    })

    initial_state: AgentState = {
        "messages": [],
        "task": task_text,
        "model": model,
        "agents_md": "",
        "agents_md_path": "",
        "dfs_tree": "",
        "preread_files": {},
        "past_mistakes": [],
        "react_trace": [],
        "files_used": [],
        "final_answer": "",
        "final_code": "",
        "verification_passed": False,
        "verification_reason": "",
        "step_count": 0,
        "inline_verify_attempts": 0,
    }

    ctx_deps = GraphContext(vm=vm, client=client, logger=logger, task_id=task_id)

    final_state = _GRAPH.invoke(initial_state, context=ctx_deps)

    logger.write_result({
        "task_id": task_id,
        "final_answer": final_state.get("final_answer", ""),
        "final_code": final_state.get("final_code", ""),
        "verification_passed": final_state.get("verification_passed", False),
        "verification_reason": final_state.get("verification_reason", ""),
        "step_count": final_state.get("step_count", 0),
        "files_used": final_state.get("files_used", []),
        "ts": time.time(),
    })

    return PipelineContext(
        task=task_text,
        model=model,
        agents_md=final_state.get("agents_md", ""),
        agents_md_path=final_state.get("agents_md_path", ""),
        dfs_tree=final_state.get("dfs_tree", ""),
        preread_files=final_state.get("preread_files", {}),
        past_mistakes=final_state.get("past_mistakes", []),
        react_trace=final_state.get("react_trace", []),
        files_used=final_state.get("files_used", []),
        final_answer=final_state.get("final_answer", ""),
        final_code=final_state.get("final_code", ""),
        verification_passed=final_state.get("verification_passed", False),
        verification_reason=final_state.get("verification_reason", ""),
    )
