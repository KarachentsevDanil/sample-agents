# Context Builder — Staff GenAI Engineer Analysis

## What It Actually Does (Step by Step)

```
ContextBuilderStage.execute()
│
├─ 1. _fetch_agents_md()          → 1 VM read     (AGENTS.MD / AGENTS.md)
├─ 2. _fetch_dfs()                → 1 VM tree call (all file paths as flat list)
│
├─ 3. _extract_rules()            → 1 LLM call     [rules_extraction/v1]
│      Input:  task (first 150 chars) + root AGENTS.md + dfs_tree
│      Output: { referenced_files: [...], key_rules: [...] }
│      Cached by: sha256(agents_md + "|TASK|" + task[:150])
│
├─ 4. _select_context_blocks()    → 0 LLM calls    (keyword heuristics)
│
├─ 5. _mandatory_preread()        → 0 LLM calls    (hardcoded path list)
│      Checks 6 hardcoded paths against filesystem tree
│
├─ 6. _suggest_files()            → 1 LLM call     [context/v2]
│      Input:  task + AGENTS.md + dfs_tree
│      Output: { files_to_read: [...] }   (max 8)
│
├─ 7. Merge: rules_files + mandatory + suggested → cap at 8 paths
├─ 8. _read_files()               → up to 8 VM reads
└─ 9. load_past_mistakes()
```

**Total overhead per task: 2 LLM calls + 2 VM reads + tree call + up to 8 file reads**

---

## Problems

### Problem 1: Reference traversal is single-level

`rules_extraction/v1` reads only the **root AGENTS.md** and extracts its references. But the root AGENTS.md almost always points to other AGENTS.md files in subdirectories, which in turn point to process docs, templates, and checklists.

```
Root AGENTS.MD
   └─ references 02_distill/AGENTS.md       ← extracted
                   └─ references 99_process/document_capture.md  ← MISSED
                   └─ references 02_distill/cards/_card-template.md  ← MISSED (unless hardcoded)
```

The agent enters the ReAct loop missing the actual process rules. It has to discover them lazily — wasting 2–5 steps that could have been avoided. For a `simple` task with a 10-step budget, those are 20–50% of the available steps.

### Problem 2: LLM used to do what a regex can do

`rules_extraction/v1` instructs an LLM to:
> "Extract every file path explicitly mentioned in AGENTS.md"

This is text parsing. Markdown link extraction is ~5 lines of regex. Using an LLM here:
- Adds 500–2000ms latency
- Introduces hallucination risk (inventing paths, dropping real ones)
- Costs tokens on every task even when AGENTS.md hasn't changed
- Has non-deterministic output (same input can produce different referenced_files)

The cache helps with repeated calls for the **same AGENTS.md + same task prefix**, but in a benchmark with 22 distinct tasks, the cache never hits. Each task has a unique first 150 chars. The cache is useless in practice.

### Problem 3: Double source of hardcoded paths

Hardcoded paths appear in **two separate places**:

In `context.py` (code):
```python
MANDATORY_PREREAD_PATTERNS = [
    "90_memory/Soul.md",
    "02_distill/AGENTS.md",
    "99_process/process_tasks.md",
    "99_process/document_capture.md",
    "02_distill/cards/_card-template.md",
    "02_distill/threads/_thread-template.md",
]
```

In `context/v2.yaml` (LLM prompt):
```
PRIORITY (include these first if they exist in the tree):
1. 90_memory/Soul.md — agent personality and constraints
2. Any AGENTS.md files in subdirectories (e.g., 02_distill/AGENTS.md)
3. Files in 99_process/ — workflow definitions
4. Template files (_card-template.md, _thread-template.md) ...
```

These are the **same paths hardcoded twice** — once as literal Python strings, once as LLM instructions. If the vault structure changes (a different benchmark config, a different contestant's vault), both places break independently. They're also task-agnostic: on a task like "Email John a digest", the agent still pre-reads `02_distill/cards/_card-template.md` for no reason.

### Problem 4: LLM suggestions are not existence-validated

The `_filter_existing()` method is applied to `extraction.referenced_files` (from rules_extraction), but NOT to the output of `_suggest_files()`. If the context/v2 LLM hallucinates a path (or suggests a path that existed in training data but not this vault), `_read_files()` will silently skip it with a `ConnectError` — consuming a slot from the 8-file cap.

```python
# context.py line 58: only rules_files get filtered
rules_files = self._filter_existing(extraction.referenced_files, ctx.dfs_tree)  # filtered
suggested = self._suggest_files(ctx, logger)  # NOT filtered against tree
all_paths = list(dict.fromkeys(rules_files + mandatory + suggested))[:MAX_PREREAD_FILES]
```

### Problem 5: Cache key is wrong

The cache key is `sha256(agents_md + "|TASK|" + task[:150])`. This means every task with a different first 150 characters produces a different cache entry — even though `referenced_files` in AGENTS.md is task-independent.

The referenced files in AGENTS.md are a property of the **vault** (the benchmark environment), not the task. Only `key_rules` (3–7 most relevant rules) is task-specific. Mixing them in one cached response and keying by task text means:
- The expensive part (file traversal) is never reused
- Tasks that share a task prefix (e.g., all "Process the next inbox file" variants) do reuse — but those are the simplest tasks

### Problem 6: 8-file cap is a correctness bottleneck

The cap is `MAX_PREREAD_FILES = 8`. The merge order is:
```
rules_files > mandatory > suggested
```

If AGENTS.md references 3 files and mandatory has 6, that's already 9 before suggestions are even considered. The 9th file is silently dropped. The agent will encounter a file reference during the ReAct loop, have to read it there, and spend a step doing so — or miss it entirely and produce wrong output.

For the capture+distill task type (t03, t07, t18–t22), the correct rule set likely requires:
1. root AGENTS.md
2. 02_distill/AGENTS.md (sub-rules)
3. 90_memory/Soul.md (personality constraints)
4. 99_process/document_capture.md (capture process)
5. 02_distill/cards/_card-template.md (card format)
6. 02_distill/threads/_thread-template.md (thread format)

That's 6 files for a capture task. Add 2 task-specific files and the cap is hit with zero headroom.

### Problem 7: `_suggest_files` is a redundant LLM call

The context/v2 LLM is given: task + AGENTS.md + dfs_tree. It's asked to pick files to pre-read. But:
- AGENTS.md already says which files to read (its references)
- The dfs_tree already contains all valid paths
- The heuristics in `MANDATORY_PREREAD_PATTERNS` already cover the common cases

The LLM adds noise: it might prioritize files based on its training priors ("Soul.md sounds important") rather than actual AGENTS.md instructions. It might pick `01_capture/influential/` (a data directory) instead of `02_distill/cards/_card-template.md` (the template that defines output format).

---

## The Core Insight

**The reference graph is already fully encoded in the AGENTS.md files themselves.** Every file the agent needs is reachable by parsing markdown links and path mentions, then recursively following those links. No LLM is needed for this step.

The current code treats this as an LLM comprehension problem ("what files are relevant?") when it's actually a graph traversal problem ("what files are reachable from the root?"). Graph traversal is deterministic, instant, and correct by construction.

---

## Proposed Architecture: `DependencyGraphBuilder`

### Algorithm

```
Input:
  - root AGENTS.md content
  - filesystem tree (flat path list)

Step 1: Parse root AGENTS.md for file references (regex)
  → candidate paths: {markdown links} ∪ {backtick paths} ∪ {bare slash-paths}
  → validate against tree: remove non-existent paths
  → queue = [validated paths], visited = {AGENTS.md}

Step 2: BFS traversal (max depth 3, max total files 20)
  For each path in queue:
    - Read file from VM
    - Parse its content for further references
    - Add new unvisited references to queue
    - Add to graph: {path → {content, refs, depth}}
  Stop when queue empty or limits hit

Step 3: Output
  graph = {
    "AGENTS.md": {content: "...", refs: ["02_distill/AGENTS.md", "90_memory/Soul.md"]},
    "02_distill/AGENTS.md": {content: "...", refs: ["99_process/document_capture.md", ...]},
    "90_memory/Soul.md": {content: "...", refs: []},
    "99_process/document_capture.md": {content: "...", refs: ["02_distill/cards/_card-template.md"]},
    ...
  }
  ordered_paths = [BFS order, shallowest first]
```

### Reference Parser (deterministic, no LLM)

```python
import re

_INLINE_LINK   = re.compile(r'\[.*?\]\(([^\)#?]+)\)')      # [text](path)
_BACKTICK_PATH = re.compile(r'`([a-zA-Z0-9_][a-zA-Z0-9_/.-]+\.[a-zA-Z]{1,5})`')
_BARE_PATH     = re.compile(r'(?<!\w)([a-zA-Z0-9_][a-zA-Z0-9_/-]*\.[a-zA-Z]{2,5})(?!\w)')

def extract_refs(content: str, tree_set: set[str]) -> list[str]:
    candidates = set()
    for m in _INLINE_LINK.finditer(content):
        candidates.add(m.group(1).lstrip('./').rstrip('/'))
    for m in _BACKTICK_PATH.finditer(content):
        candidates.add(m.group(1))
    for m in _BARE_PATH.finditer(content):
        p = m.group(1)
        if '/' in p:  # require directory component — avoids matching "Soul.md" standalone
            candidates.add(p)
    return [p for p in candidates if p in tree_set]
```

### What Gets Passed to the Planner

Instead of `preread_files: dict[str, str]` (a flat dict of file contents), the planner receives a structured graph:

```python
@dataclass
class ReferenceGraph:
    nodes: dict[str, GraphNode]      # path → {content, refs, depth}
    ordered_paths: list[str]         # BFS order: shallowest dependencies first
    root_content: str                # root AGENTS.md content (fast access)
    total_files: int
    max_depth_reached: int
```

The planner prompt becomes:
```
Task: {task}

[Dependency Graph — all rules in scope]
Root AGENTS.MD:
{root_agents_md_content}

Referenced rule files (loaded recursively):
02_distill/AGENTS.md (depth 1):
{content}

90_memory/Soul.md (depth 1):
{content}

99_process/document_capture.md (depth 2, via 02_distill/AGENTS.md):
{content}

...
```

The planner now sees the **complete rule set** and can:
1. Identify which rules apply to this specific task
2. Identify which files the ReAct agent will need to read during execution
3. Estimate complexity more accurately
4. Create plan steps that reference the correct process doc

---

## LLM Call Comparison

| Stage | Current calls | Proposed calls |
|---|---|---|
| Rules extraction (context building) | 1 LLM | **0** (regex) |
| File suggestion (context building) | 1 LLM | **0** (graph traversal) |
| Planning | 1 LLM (richer input) | 1 LLM |
| ReAct loop | 1 session | 1 session |
| Verifier | 1 LLM | 1 LLM |
| **Total** | **5 LLM calls** | **3 LLM calls** |

**40% reduction in LLM overhead per task.** The 2 eliminated calls are replaced by deterministic operations that are faster AND more reliable.

---

## What Changes vs. What Stays

| Component | Action | Why |
|---|---|---|
| `_fetch_agents_md()` | Keep as-is | Correct, reads root AGENTS.md |
| `_fetch_dfs()` | Keep as-is | Needed for tree validation |
| `_extract_rules()` + `rules_extraction/v1` | **Delete** | Replaced by regex parser |
| `_suggest_files()` + `context/v2.yaml` | **Delete** | Replaced by graph traversal |
| `MANDATORY_PREREAD_PATTERNS` | **Delete** | All references discovered dynamically |
| `MAX_PREREAD_FILES = 8` cap | **Raise to 20** | BFS already limits depth; cap should not truncate graph |
| `_select_context_blocks()` | Keep as-is | Keyword heuristics, fast, zero cost |
| `load_past_mistakes()` | Keep as-is | Correct |
| `RulesExtraction` model | **Delete** | Replaced by `ReferenceGraph` |
| `FileSuggestion` model | **Delete** | No longer needed |
| New: `ReferenceGraph` + `GraphNode` models | **Add** | Structured graph output |
| New: `extract_refs()` function | **Add** | Deterministic reference parser |
| New: `_traverse_graph()` method | **Add** | BFS traversal logic |

---

## What the Planner Needs to Change

Currently, `PlanningStage._build_planning_input()` uses `ctx.preread_files` (flat dict). After the redesign, it should receive `ctx.reference_graph` (structured) and format it as a dependency-ordered rule document for the planning LLM.

The planner prompt (`planning/v1.yaml`) also needs updating to consume the graph structure instead of a flat "Pre-loaded files: file1, file2" list.

---

## Edge Cases

| Case | Handling |
|---|---|
| Circular reference (A → B → A) | `visited` set prevents infinite loop |
| Reference to non-existent file | Validate against `tree_set` before queuing |
| Reference to binary/data file (`.jpg`, `.csv`) | Exclude by extension whitelist |
| Extremely deep graph (10+ hops) | Hard cap at depth 3; beyond that the rules are likely not task-critical |
| Root AGENTS.md has no references | Graph = {root only}, planner gets root rules only |
| Path with `../` or leading `/` | Normalize before lookup |

---

## Summary

The current context builder answers a **graph traversal question** with LLMs when it should use an algorithm. The hardcoded paths are a symptom of not having the graph traversal: they were added manually because the LLM kept missing the same files. The real fix is to not need those heuristics at all — by following the references AGENTS.md itself declares.

The proposed `DependencyGraphBuilder`:
1. Is **deterministic** — same AGENTS.md always produces same graph
2. Is **complete** — every referenced file is discovered, regardless of vault structure
3. Is **zero LLM cost** — pure algorithmic
4. Gives the planner **richer context** — the full rule set with dependency structure, not just 3-7 extracted snippets
5. **Transfers across benchmarks** — works for any vault that uses markdown-style file references in AGENTS.md
