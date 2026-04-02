# Anthropic biology of LLMs: use interpretability as a targeted debugging lane

- **Source:** [/01_capture/influential/2026-03-06__anthropic-biology-of-llms.md](/01_capture/influential/2026-03-06__anthropic-biology-of-llms.md)
- **Date:** 2026-03-06
- **People:** Anthropic interpretability team
- **Topics:** interpretability, model debugging, evals, reliability
- **Tags:** Cleanup

## Key Points
- Some repeated model behaviors can be investigated more deeply than prompt-level trial and error.
- Interpretability work is most justified when the same failure pattern keeps returning or the risk is high enough to warrant deeper inspection.
- Evals and trajectory data are still the first line of defense; deeper model inspection is an escalation tier.

## Why this matters for current work
- This keeps John's repo grounded: start with observable failures and evals, then use deeper analysis only when the failure class earns it.

<!-- AGENT_EDITABLE_START:reflection -->
- A useful mental model is “black-box first, white-box only when needed.” That keeps ambition high without making the workflow academic by default.
<!-- AGENT_EDITABLE_END:reflection -->
