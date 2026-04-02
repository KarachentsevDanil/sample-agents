Put things into files NUMBER.json

Use JSON like this:

```json
{
  "number": "SR-13",
  "account_id": "acct_001",
  "issued_on": "2026-03-10",
  "lines": [
    {
      "name": "OpenAI Subscription",
      "amount": 20
    },
    {
      "name": "Claude Subscription",
      "amount": 20
    }
  ],
  "total": 40
}
```

Line objects may omit fields when they are not needed.

## Invariants

- `number` must stay stable and usually matches the filename stem.
- `account_id` links the invoice back to the owning account in `accounts/`. Optional.
- `issued_on` uses `YYYY-MM-DD`; when you need the latest invoice for one account, compare this field.
- `total` should equal the sum of all line `amount` values when lines are present.
- Invoice files are typed records, not email drafts or reminders.
- Do not create an invoice just because an inbox email mentions one unless the task explicitly asks for invoice creation.
