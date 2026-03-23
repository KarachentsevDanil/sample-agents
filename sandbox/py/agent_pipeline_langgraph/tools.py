"""
Tool schema definitions for bind_tools().

These functions are NEVER called directly — they exist only to generate JSON schemas
that ChatOpenAI uses for tool-calling. Actual dispatch against vm happens in
react_tools_node (react.py).
"""
from langchain_core.tools import tool


@tool
def tree(path: str) -> str:
    """Get a recursive directory tree outline at the given folder path."""
    raise NotImplementedError("Runtime-dispatched tool")


@tool
def search(pattern: str, path: str = "/", count: int = 5) -> str:
    """Search for files/content matching the regex pattern. count must be between 1 and 10."""
    raise NotImplementedError("Runtime-dispatched tool")


@tool
def list_dir(path: str) -> str:
    """List immediate contents of a directory."""
    raise NotImplementedError("Runtime-dispatched tool")


@tool
def read(path: str) -> str:
    """Read the content of a file at the given path."""
    raise NotImplementedError("Runtime-dispatched tool")


@tool
def write(path: str, content: str) -> str:
    """Write content to a file at the given path, overwriting any existing content."""
    raise NotImplementedError("Runtime-dispatched tool")


@tool
def delete(path: str) -> str:
    """Delete the file at the given path."""
    raise NotImplementedError("Runtime-dispatched tool")


@tool
def report_completion(
    answer: str,
    grounding_refs: list[str],
    completed_steps_laconic: list[str],
    code: str = "completed",
) -> str:
    """
    Report task completion with the final answer.

    answer: the final answer string (must be non-empty).
    grounding_refs: list of file paths that contributed to the answer (no leading slash).
    completed_steps_laconic: brief bullet list of what was accomplished.
    code: 'completed' or 'failed'.
    """
    raise NotImplementedError("Runtime-dispatched tool")


ALL_TOOLS = [tree, search, list_dir, read, write, delete, report_completion]
