TOOLS = [
    {
        "name": "tree",
        "description": "Get directory tree / filesystem outline for a path",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "folder path"}},
            "required": ["path"],
        },
    },
    {
        "name": "search",
        "description": "Search for a regex pattern in files under a path",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "path": {"type": "string", "default": "/"},
                "count": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "list",
        "description": "List directory contents",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "name": "read",
        "description": "Read the contents of a file",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "name": "write",
        "description": "Write content to a file",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "delete",
        "description": "Delete a file",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "name": "report_completion",
        "description": (
            "Report task completion with the final answer. Call this when the task is done. "
            "grounding_refs MUST include: (1) the AGENTS.MD canonical path from the section header, "
            "(2) every file path you read, wrote, or deleted."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "answer": {"type": "string"},
                "completed_steps_laconic": {"type": "array", "items": {"type": "string"}},
                "grounding_refs": {"type": "array", "items": {"type": "string"}},
                "code": {"type": "string", "enum": ["completed", "failed"]},
            },
            "required": ["answer", "completed_steps_laconic", "code"],
        },
    },
]
