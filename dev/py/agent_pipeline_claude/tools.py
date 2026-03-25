TOOLS = [
    {
        "name": "tree",
        "description": "Get recursive filesystem tree from a root path",
        "input_schema": {
            "type": "object",
            "properties": {"root": {"type": "string", "description": "tree root; empty string means repository root", "default": ""}},
            "required": ["root"],
        },
    },
    {
        "name": "find",
        "description": "Find files or directories by name pattern",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "name pattern to search for"},
                "root": {"type": "string", "default": "/"},
                "kind": {"type": "string", "enum": ["all", "files", "dirs"], "default": "all"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 10},
            },
            "required": ["name"],
        },
    },
    {
        "name": "search",
        "description": "Full-text search across files",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "root": {"type": "string", "default": "/"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 10},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "list",
        "description": "List direct children of a directory",
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
        "description": "Write (create or overwrite) a file",
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
        "name": "mkdir",
        "description": "Create a directory",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "name": "move",
        "description": "Move or rename a file or directory",
        "input_schema": {
            "type": "object",
            "properties": {
                "from_name": {"type": "string"},
                "to_name": {"type": "string"},
            },
            "required": ["from_name", "to_name"],
        },
    },
    {
        "name": "report_completion",
        "description": (
            "Report task completion with the final message. Call this when the task is done. "
            "grounding_refs MUST include: (1) the AGENTS.MD canonical path from the section header, "
            "(2) every file path you read, wrote, or deleted."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "message": {"type": "string"},
                "outcome": {
                    "type": "string",
                    "enum": [
                        "OUTCOME_OK",
                        "OUTCOME_DENIED_SECURITY",
                        "OUTCOME_NONE_CLARIFICATION",
                        "OUTCOME_NONE_UNSUPPORTED",
                        "OUTCOME_ERR_INTERNAL",
                    ],
                },
                "completed_steps_laconic": {"type": "array", "items": {"type": "string"}},
                "grounding_refs": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["message", "outcome", "completed_steps_laconic"],
        },
    },
]
