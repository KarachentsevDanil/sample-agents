# Show HN: Agent Kernel - Three Markdown files that make any AI agent stateful

Captured on: 2026-03-23
HN URL: https://news.ycombinator.com/item?id=47486287
Source URL: https://github.com/oguzbilgic/agent-kernel

Raw text:

Agent Kernel presents itself as a minimal way to make an AI agent stateful. The repo says the agent remembers between sessions, takes notes, and builds on past work with no framework and no database, just three markdown files and a git repo. The memory structure shown in the README is AGENTS.md as the kernel, IDENTITY.md for who the agent is, KNOWLEDGE.md as an index, plus knowledge/ for current state and notes/ for append-only session logs.

The HN discussion is mixed. Some people like the simplicity and the “text files plus git” approach. Others say append-only notes can pollute future runs with obsolete context. One repeated criticism is that instruction files are weaker than hooks when behavior must be enforced instead of merely suggested.


