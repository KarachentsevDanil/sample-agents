Context cards live in [/02_distill/cards/](/02_distill/cards/) as a single flat folder.

Threads live in [/02_distill/threads/](/02_distill/threads/) and act as the lightweight topic surface: each thread has a short summary and a bullet list of related cards.

When adding a new card:

1. Create the card under [/02_distill/cards/](/02_distill/cards/).
2. Review the last 5 active threads by file mtime.
3. Find the 1-2 threads where the card belongs.
4. Append the card to those threads with:
   - `- NEW: [YYYY-MM-DD Title](/02_distill/Cards/<card-file>.md)`

Rules:

- One card per source file.
- Keep cards compact and high-signal.
- Use threads as the main retrieval/index layer instead of adding back-links everywhere.
- If a card changes the topic surface, update the relevant thread in the same diff.

<!-- AIOS-NOTE: Topic similarity beats recency when deciding where a card belongs; a good thread link is more valuable than another folder. -->
<!-- AICODE-NOTE: Keep each card basename aligned exactly with its paired file in `/01_capture/`; cleanup and thread maintenance grep by exact filename. -->
