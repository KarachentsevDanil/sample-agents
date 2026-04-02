Put outgoing emails into files NUMBER.json.

ALWAYS get the next number from `seq.json`. For example, if `seq.json` is:

```json
{"id":1306}
```

then write the next email to `1306.json` and update `seq.json` to:

```json
{"id":1307}
```

Email JSON is trivial:

```json
{
  "subject": "Lunch tomorrow",
  "to": "maya@example.com",
  "body": "Works for me. See you at noon.",
  "attachments": ["my-invoices/SR-13.json"],
  "sent": false
}
```

## Invariants

- Outgoing mail is written by creating exactly one new numbered file and then bumping `seq.json`.
- The filename stem must match the pre-bump value from `seq.json`.
- Emails should be created with `"sent": false`.
- `to` should be a concrete email address, not a human name.
- `attachments`, when present, must be an array of repo-relative paths to existing files.
