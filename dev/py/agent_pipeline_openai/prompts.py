def build_initial_user_message(
    task: str,
    agents_md: str,
    agents_md_path: str,
    dfs_tree: str,
    preread_files: dict,
    past_mistakes: list,
) -> str:
    parts = [f"[TASK]\n{task}"]

    if agents_md:
        label = f"AGENTS.MD (canonical path: {agents_md_path})" if agents_md_path else "AGENTS.MD"
        parts.append(f"[{label} — mandatory instructions]\n{agents_md}")

    if dfs_tree:
        parts.append(f"[Filesystem outline — DFS from /]\n{dfs_tree}")

    for path, content in preread_files.items():
        parts.append(f"[Pre-read file: {path}]\n{content}")

    if past_mistakes:
        lines = "\n".join(
            f"- {m.get('reason', 'unknown failure')}" for m in past_mistakes
        )
        parts.append(f"[Past mistakes on this task — do not repeat]\n{lines}")

    return "\n\n".join(parts)
