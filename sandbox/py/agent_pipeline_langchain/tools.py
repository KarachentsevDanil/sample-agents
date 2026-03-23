from typing import Annotated, Literal

from pydantic import BaseModel, Field

try:
    from langchain_core.tools import tool
except ImportError:
    tool = None


class TreeArgs(BaseModel):
    path: str = Field(..., description="Folder path to inspect.")
    reason: str = Field(default="", description="Why this tool is needed for the next step.")


class SearchArgs(BaseModel):
    pattern: str = Field(..., description="Text pattern to search for.")
    path: str = Field(default="/", description="Folder path to search under.")
    count: int = Field(default=5, ge=1, le=10, description="Maximum number of matches.")
    reason: str = Field(default="", description="Why this tool is needed for the next step.")


class ListArgs(BaseModel):
    path: str = Field(..., description="Folder path to list.")
    reason: str = Field(default="", description="Why this tool is needed for the next step.")


class ReadArgs(BaseModel):
    path: str = Field(..., description="File path to read.")
    reason: str = Field(default="", description="Why this tool is needed for the next step.")


class WriteArgs(BaseModel):
    path: str = Field(..., description="File path to write.")
    content: str = Field(..., description="Full file contents to write.")
    reason: str = Field(default="", description="Why this tool is needed for the next step.")


class DeleteArgs(BaseModel):
    path: str = Field(..., description="File path to delete.")
    reason: str = Field(default="", description="Why this tool is needed for the next step.")


class ReportCompletionArgs(BaseModel):
    answer: str = Field(..., description="Final answer for the task.")
    code: Literal["completed", "failed"] = Field(..., description="Task completion code.")
    completed_steps_laconic: list[str] = Field(
        default_factory=list,
        description="Short list of completed steps.",
    )
    grounding_refs: list[str] = Field(
        default_factory=list,
        description="Relative file paths that grounded the answer.",
    )
    reason: str = Field(default="", description="Why the task is ready to complete.")


if tool is not None:
    @tool("tree", args_schema=TreeArgs)
    def tree(path: Annotated[str, "Folder path"], reason: Annotated[str, "Why this is needed"] = "") -> str:
        """Get a DFS-style outline for a folder path."""
        raise NotImplementedError("Runtime-dispatched tool")


    @tool("search", args_schema=SearchArgs)
    def search(
        pattern: Annotated[str, "Pattern to search"],
        path: Annotated[str, "Folder path"] = "/",
        count: Annotated[int, "Maximum number of matches"] = 5,
        reason: Annotated[str, "Why this is needed"] = "",
    ) -> str:
        """Search for text under a path."""
        raise NotImplementedError("Runtime-dispatched tool")


    @tool("list", args_schema=ListArgs)
    def list_dir(path: Annotated[str, "Folder path"], reason: Annotated[str, "Why this is needed"] = "") -> str:
        """List files in a folder."""
        raise NotImplementedError("Runtime-dispatched tool")


    @tool("read", args_schema=ReadArgs)
    def read(path: Annotated[str, "File path"], reason: Annotated[str, "Why this is needed"] = "") -> str:
        """Read a file."""
        raise NotImplementedError("Runtime-dispatched tool")


    @tool("write", args_schema=WriteArgs)
    def write(
        path: Annotated[str, "File path"],
        content: Annotated[str, "Full file contents"],
        reason: Annotated[str, "Why this is needed"] = "",
    ) -> str:
        """Write a file."""
        raise NotImplementedError("Runtime-dispatched tool")


    @tool("delete", args_schema=DeleteArgs)
    def delete(path: Annotated[str, "File path"], reason: Annotated[str, "Why this is needed"] = "") -> str:
        """Delete a file."""
        raise NotImplementedError("Runtime-dispatched tool")


    @tool("report_completion", args_schema=ReportCompletionArgs)
    def report_completion(
        answer: Annotated[str, "Final answer"],
        code: Annotated[str, "Completion code"],
        completed_steps_laconic: Annotated[list[str], "Completed steps"],
        grounding_refs: Annotated[list[str], "Grounding refs"] | None = None,
        reason: Annotated[str, "Why the task is ready to complete"] = "",
    ) -> str:
        """Submit the final answer and stop the task."""
        raise NotImplementedError("Runtime-dispatched tool")


    LANGCHAIN_TOOLS = [tree, search, list_dir, read, write, delete, report_completion]
else:
    LANGCHAIN_TOOLS = []

TOOL_SCHEMAS = {
    "tree": TreeArgs,
    "search": SearchArgs,
    "list": ListArgs,
    "read": ReadArgs,
    "write": WriteArgs,
    "delete": DeleteArgs,
    "report_completion": ReportCompletionArgs,
}
