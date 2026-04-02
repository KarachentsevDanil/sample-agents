Put account records into files `ACCOUNT_ID.json`.

Example filename:

```text
acct_001.json
```

Use JSON like this:

```json
{
  "id": "acct_001",
  "name": "Nordlicht Health",
  "legal_name": "Nordlicht Health GmbH",
  "industry": "healthcare",
  "region": "DACH",
  "country": "Germany",
  "tier": "strategic",
  "status": "active",
  "primary_contact_id": "cont_001",
  "account_manager": "John Novak",
  "last_contacted_on": "2026-03-02",
  "next_follow_up_on": "2026-03-18",
  "notes": "Early design partner with slow procurement.",
  "compliance_flags": ["dpa_required", "security_review_open"]
}
```

Optional fields may be omitted when empty.

Dates use `YYYY-MM-DD`.

## Invariants

- `id` must stay stable and match the filename stem.
- `primary_contact_id` should point to an existing contact in `contacts/`.
- `tier` is a small working category such as `strategic`, `growth`, or `core`.
- `status` is a small lifecycle marker such as `active`, `paused`, or `archived`.
- `compliance_flags` is an open-ended list of guard flags. Preserve unknown values.
- Some tasks rely on flags such as `security_review_open` or `external_send_guard`.
- `next_follow_up_on` is the account-level follow-up date. If a reminder carries the same workflow date, keep them aligned when rescheduling.


## Important!

Compliance flags are kept called this way for historical reasons. Now we can also expand them to use as general account flags.

Most notable could be:

- security_review_open - pending security review
- ai_insights_subscriber - VIP customer, subscribed to our Monthly AI Insights. Might need to check up on them, if they need a follow-up
