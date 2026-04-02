Be pragmatic. Prefer small diffs, direct language, and low process overhead.

This repository is a minimal working knowledge repo with a strict pipeline:

- `00_inbox/` collects unprocessed drops. Treat its contents as unfiltered input.
- `01_capture/` holds canonical captured sources in repo format.
- `02_distill/` contains editable synthesis:
  - `cards/` is a single flat folder of distilled notes.
  - `threads/` is the topic index that links cards together.
- `04_projects/` holds concrete deliverables and working artifacts.
- `07_rfcs/` holds larger proposals that need clear reasoning before action.
- `90_memory/` is the agent control center.
- `99_process/` is the source of truth for repo processes. To see what exists, run `tree 99_process` or `ls 99_process/`.

Rules:

- Always read [/90_memory/Soul.md](/90_memory/Soul.md) when starting a new session.
- Prefer threads -> cards -> capture when looking for context.
- Keep existing files in `01_capture/` immutable.
- Use repo-root Markdown links when linking files.
- Avoid creating extra coordination layers unless they clearly reduce review effort.

Respect `AGENTS.md` (global and in nested folder): modify only declared `AGENT_EDITABLE` blocks (or sections permitted in workflow), avoid reflow/reordering, never rewrite existing files in `01_capture/`, and prefer small, ID-stable changes that are easy to review. When adding a card under [/02_distill/cards/](/02_distill/cards/), also update 1–2 relevant threads under [/02_distill/threads/](/02_distill/threads/) (append a `NEW:` bullet). For any workflow question, inspect [/99_process/](/99_process/).

<!-- AIOS-NOTE: Keep the control center small. Once agent coordination spreads across many files, it turns into ceremony and stops helping. -->
