# process_tasks

Use this when you want an agent to pick up one useful task and execute it with minimal ceremony.

## 0) Load soul

Read: [/90_memory/Soul.md](/90_memory/Soul.md)

## 1) Enter the control center

Read:

- [/90_memory/agent_preferences.md](/90_memory/agent_preferences.md)
- [/90_memory/agent_initiatives.md](/90_memory/agent_initiatives.md)
- [/90_memory/Agent_changelog.md](/90_memory/Agent_changelog.md) (recent lines only)

## 2) Pick one task

Choose one concrete bullet from `agent_initiatives.md`.

If the task is vague, rewrite it into:

- expected output
- done when

## 3) Run the task diff-first

- Prefer small diffs.
- Avoid rewrites.
- Default context lookup: threads -> cards -> capture.
- For inbox items worth keeping, follow [/99_process/document_capture.md](/99_process/document_capture.md).
- For pruning low-value cards, follow [/99_process/document_cleanup.md](/99_process/document_cleanup.md).

## 4) Close the task

Only if the outcome is meaningful, append one line to [/90_memory/agent_changelog.md](/90_memory/agent_changelog.md).

## 5) Escalation

- Safe maintenance: do it.
- New artifacts: default to review-first.
- Process changes: discuss before broadening the workflow.
