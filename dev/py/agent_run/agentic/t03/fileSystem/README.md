
This is a small knowledge-to-output repo template for an aspiring AI engineer - John

## Current shape

This starter repo clusters around three loops:

- distill research and conversations into cards + threads
- turn those distilled notes into project outputs
- use RFCs for bigger shifts in agent workflow or storage design

That means the repo behaves more like a knowledge-to-output pipeline than a passive archive.

## Essentials

- `01_capture` - keep canonical captured sources here
- `02_distill/` is the main operating surface: small cards in a flat folder, grouped by lightweight topic threads.
- `04_projects/` holds concrete outputs that ship or get handed off.
- `07_rfcs/` is for larger design changes that need a clearer argument before implementation.
- `90_memory/` is the control center for how an agent works in the repo.
- `99_process/` keeps the repo processes. Inspect that folder instead of relying on a catalogue here.

## What was removed

- Almost all real content.
- Current project payloads, captures, newsletters, and course material.
- PoCs, images, and tool-specific experiments that were active recently but are not required to understand the operating model.

## Included folders

- `00_inbox/` for unprocessed drops.
- `01_capture/` for canonical captured source material in repo format.
- `02_distill/cards/` for distilled notes.
- `02_distill/threads/` for topic-level retrieval surfaces.
- `04_projects/` for deliverables.
- `07_rfcs/` for proposals.
- `90_memory/` for agent memory and constraints.
- `99_process/` for lightweight SOPs.

## Start here

1. Read [/90_memory/Soul.md](/90_memory/Soul.md).
2. Read [/AGENTS.md](/AGENTS.md).
3. Run `tree 99_process` or `ls 99_process/`, then follow the relevant process file.

<!-- AIOS-NOTE: The recent repo pattern is not “capture everything”; it is “distill aggressively, then use threads as the main retrieval surface.” -->
