# OpenAI harness engineering: the bottleneck moves from typing to review bandwidth

- **Source:** [/01_capture/influential/2026-02-15__openai-harness-engineering.md](/01_capture/influential/2026-02-15__openai-harness-engineering.md)
- **Date:** 2026-02-15
- **People:** Ryan Lopopolo
- **Topics:** harness engineering, agent legibility, repository design, review loops

## Key Points
- The useful abstraction is not “agent writes code” but “humans shape the environment so agents can write code safely.”
- Once throughput rises, the bottleneck becomes review, QA, and system legibility.
- Architecture constraints, docs, and repository structure become part of the runtime for the agent, not just background documentation.

## Why this matters for current work
- This is probably the clearest public example of what an AI engineer actually does once the model is already competent enough to produce large volumes of code.

<!-- AGENT_EDITABLE_START:reflection -->
- The non-obvious shift is organizational: good agent teams behave more like platform engineers than like heroic prompt tinkerers.
<!-- AGENT_EDITABLE_END:reflection -->
