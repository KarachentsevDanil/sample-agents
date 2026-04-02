# Hacker News notes: structured outputs need schema design and evals

- **HN discussion:** [Sampling and structured outputs in LLMs](https://news.ycombinator.com/item?id=45345207)
- **Related comments:** [property ordering gotcha](https://news.ycombinator.com/item?id=45347100), [error-tolerant parsing discussion](https://news.ycombinator.com/item?id=45347840)
- **Captured on:** 2026-03-23
- **Why keep this:** it adds practitioner friction that polished vendor docs usually skip

## Raw notes

- Structured outputs are useful, but they do not remove the need for validation and evals.
- Schema details can change behavior more than people expect; field ordering and nested object shape can affect generation quality.
- Some teams prefer error-tolerant parsing over hard constrained decoding, especially when they want reasoning-rich outputs.
- The practical engineering question is not “structured or unstructured,” but which failure mode is easier to observe and repair in your system.

<!-- AIOS-NOTE: This is useful because it turns structured outputs from a marketing checkbox into a design tradeoff. -->
