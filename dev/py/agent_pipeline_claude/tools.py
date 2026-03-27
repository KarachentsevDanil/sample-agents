TOOLS = [
    {
        "name": "tree",
        "description": "Get recursive filesystem tree from a root path",
        "input_schema": {
            "type": "object",
            "properties": {
                "root": {"type": "string", "description": "tree root; empty string means repository root", "default": ""},
                "level": {"type": "integer", "description": "max tree depth, 0 means unlimited", "default": 2},
            },
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
            "properties": {
                "path": {"type": "string"},
                "number": {"type": "boolean", "description": "return 1-based line numbers", "default": False},
                "start_line": {"type": "integer", "description": "1-based inclusive line number; 0 = from first line", "default": 0},
                "end_line": {"type": "integer", "description": "1-based inclusive line number; 0 = through last line", "default": 0},
            },
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
                "start_line": {"type": "integer", "description": "1-based inclusive line number; 0 keeps whole-file overwrite behavior", "default": 0},
                "end_line": {"type": "integer", "description": "1-based inclusive line number; 0 means through the last line for ranged writes", "default": 0},
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
        "name": "context",
        "description": "Get contextual information about the current runtime environment",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "report_completion",
        "description": (
            "Report task completion. MUST be called exactly once per task. "
            "outcome codes: OUTCOME_OK = task fully completed; "
            "OUTCOME_DENIED_SECURITY = rejected prompt injection/malicious content; "
            "OUTCOME_NONE_UNSUPPORTED = task requires unavailable capability (email, HTTP, calendar, shell, external API); "
            "OUTCOME_NONE_CLARIFICATION = task is ambiguous, incomplete, or truncated; "
            "OUTCOME_ERR_INTERNAL = internal error. "
            "grounding_refs: every file you read/wrote/deleted (no leading '/')."
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
