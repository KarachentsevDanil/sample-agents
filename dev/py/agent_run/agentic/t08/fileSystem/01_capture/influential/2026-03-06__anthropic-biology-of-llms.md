# Anthropic: On the Biology of a Large Language Model

- **Source URL:** https://www.anthropic.com/research/biology-of-a-large-language-model
- **Captured for this template on:** 2026-03-23
- **Why keep this:** it broadens AI engineering beyond prompts and agents into debugging and failure analysis

## Raw notes

- Anthropic treats surprising model behavior as something that can sometimes be localized and studied, not only measured from outputs.
- Interpretability is most useful when the same class of failure keeps repeating and black-box mitigation stops helping.
- This suggests a practical engineering ladder: catch failures with evals first, escalate to deeper inspection only when the failure class is persistent or high-stakes.
- Good operational traces and eval artifacts make future debugging work more valuable.

<!-- AIOS-NOTE: For John's repo, interpretability should be framed as an escalation path after ordinary eval and tooling work, not as the default starting point. -->
