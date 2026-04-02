# document_cleanup

Use this when the repo has low-value distill artifacts that should be removed cleanly.

Current marker:

- cards marked with `- **Tags:** Cleanup`

## Scope

- Remove cleanup-tagged cards from `/02_distill/`
- Remove their thread links
- Keep `/01_capture/` untouched unless a human explicitly asks otherwise
- Treat template files such as `_card-template.md` and `_thread-template.md` as scaffolding, not captured content

## Steps

1. Find candidates:
   - `rg -n '\*\*Tags:\*\* Cleanup' 02_distill/cards`
2. Sanity-check each card:
   - Is it thin, redundant, stale, or weak relative to the rest of the starter pack?
   - If not, remove the tag instead of deleting the card.
3. Remove thread references:
   - search `/02_distill/threads/` for the card filename
   - delete matching bullets cleanly
4. Delete the card file from `/02_distill/cards/`
5. Verify cleanup is complete:
   - no thread links remain
   - `rg` no longer finds the cleanup-tagged card path in `/02_distill/`

## Done when

- cleanup-tagged cards are either deleted or intentionally de-tagged
- threads no longer point at deleted cards
- `/01_capture/` remains intact

<!-- AIOS-NOTE: In this repo, cleanup means pruning distill artifacts while keeping source captures recoverable; do not turn cleanup into source deletion by default. -->
<!-- AICODE-NOTE: Distill templates are repo scaffolding. They are not knowledge artifacts and should not be treated as captured cards or threads during cleanup/reset work. -->
