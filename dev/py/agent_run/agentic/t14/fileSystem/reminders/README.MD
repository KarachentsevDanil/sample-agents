Put reminder records into files `REMINDER_ID.json`.

Example filename:

```text
rem_001.json
```

Use JSON like this:

```json
{
  "id": "rem_001",
  "account_id": "acct_001",
  "contact_id": "cont_001",
  "due_on": "2026-03-18",
  "title": "Follow up with Nordlicht Health",
  "kind": "follow_up",
  "status": "open",
  "priority": "high",
  "description": "Check pipeline health and confirm the next concrete step."
}
```

Optional fields may be omitted when empty.

Dates use `YYYY-MM-DD`.

## Invariants

- `id` must stay stable and match the filename stem.
- `account_id` must point to an existing account in `accounts/`.
- `contact_id`, when present, must point to an existing contact and should belong to the same account.
- `kind` is a small workflow category such as `follow_up`, `invoice_request`, or `todo`.
- `status` is a small lifecycle marker such as `open`, `done`, or `cancelled`.
- `priority` is a small ordered set such as `low`, `medium`, `high`.
- `due_on` is used for overdue checks. If the owning account also carries the same follow-up date, keep them aligned when rescheduling.
