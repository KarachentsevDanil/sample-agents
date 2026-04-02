# Hacker News: structured outputs still need schema design and evals

- **Source:** [/01_capture/influential/2026-03-23__hn-structured-outputs-practical-notes.md](/01_capture/influential/2026-03-23__hn-structured-outputs-practical-notes.md)
- **Date:** 2026-03-23
- **People:** Hacker News discussion
- **Topics:** structured outputs, schema design, parsing, evals, LLM engineering
- **Tags:** Cleanup

## Key Points
- Structured outputs help, but they do not eliminate validation work.
- Schema details such as field ordering and nested object shape can affect model behavior in surprising ways.
- There is a real tradeoff between constrained decoding and error-tolerant parsing; the right choice depends on which failures are easiest to detect and repair.

## Why this matters for current work
- This gives John a more realistic engineering stance: treat structured outputs as a reliability tool with tradeoffs, not as a solved checkbox.

<!-- AGENT_EDITABLE_START:reflection -->
- The valuable part of the HN thread is not the ideology; it is the field reports about what actually breaks in production.
<!-- AGENT_EDITABLE_END:reflection -->
