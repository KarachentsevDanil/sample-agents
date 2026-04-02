Put opportunity records into files `OPPORTUNITY_ID.json`.

Example filename:

```text
opp_001.json
```

Use JSON like this:

```json
{
  "id": "opp_001",
  "account_id": "acct_001",
  "name": "Nordlicht Health expansion",
  "stage": "negotiation",
  "amount_eur": 52000,
  "owner": "John Novak",
  "probability_percent": 70,
  "last_activity_on": "2026-03-01",
  "target_close_on": "2026-05-14",
  "next_action": "send follow-up draft with next-step options",
  "risk_flags": ["security_review_open", "legal_waiting_on_customer"]
}
```

Optional fields may be omitted when empty.

Dates use `YYYY-MM-DD`.

## Invariants

- `id` must stay stable and match the filename stem.
- `account_id` must point to an existing account in `accounts/`.
- `stage` is a small controlled set: `lead`, `qualified`, `proposal`, `negotiation`, `won`, `lost`.
- `probability_percent` is an integer from `0` to `100`.
- `risk_flags` is open-ended. Preserve unknown values.
- Some tasks rely on `security_review_open` appearing in `risk_flags`.
- `next_action` is the current human next step, not an audit log.
