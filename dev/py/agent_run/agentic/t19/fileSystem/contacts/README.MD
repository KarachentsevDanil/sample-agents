Put contact records into files `CONTACT_ID.json`.

Example filename:

```text
cont_001.json
```

Use JSON like this:

```json
{
  "id": "cont_001",
  "account_id": "acct_001",
  "full_name": "Sophie Muller",
  "role": "Operations Director",
  "email": "sophie.muller@nordlicht-health.example.com",
  "preferred_tone": "brief",
  "last_seen_on": "2026-03-10",
  "tags": ["finance", "billing"]
}
```

Optional fields may be omitted when empty.

Dates use `YYYY-MM-DD`.

## Invariants

- `id` must stay stable and match the filename stem.
- `account_id` must point to an existing account in `accounts/`.
- `email` is the safest concrete identifier for a person-level answer.
- `preferred_tone` is a lightweight writing hint such as `brief`, `warm`, `formal`, or `direct`.
- `tags` is open-ended. Preserve unknown values instead of normalizing them away.
