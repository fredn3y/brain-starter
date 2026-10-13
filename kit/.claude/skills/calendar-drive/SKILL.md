---
name: calendar-drive
description: Use when the user asks about their schedule, free time or when to meet (read-only - never creates or changes events), or wants to find, read or summarise a Google Drive file such as a Doc, Sheet or Slides deck.
---

# Calendar and Drive

Read only. You can't create, move or accept calendar events, or change or share Drive files.

## Calendar

```bash
gws calendar +agenda --today --format json --timezone <the user's timezone>
```

Instead of `--today`: `--tomorrow`, `--week` or `--days N`. The user's timezone is `timezone` in
`.kit/config.json`. The agenda covers all their calendars.

### Finding a time

1. Get the days in question (`--days 7` for "next week or so").
2. The user's free time = their working hours (`work_hours` in `.kit/config.json`, and `me.md`)
   minus events. Keep 15 minutes either side of meetings. Avoid their protected time in `me.md`.
3. Offer two or three options, for example "Tue 14 Oct 10:00-10:30 or Wed 15 Oct 15:00-15:30".
   If the other person is in another timezone, show both times.
4. To send them: draft the email or Slack message (`drafting` skill). Never create the event; the
   user sends the proposal and books it themselves. Check the calendar again just before they send.

## Drive

### Find

```bash
gws drive files list --params "{\"q\": \"name contains 'budget' and trashed = false\", \"pageSize\": 20, \"fields\": \"files(id,name,mimeType,modifiedTime,webViewLink)\"}"
```

Other searches for `q`:
- words inside files: `fullText contains 'pricing plan' and trashed = false`
- only Docs: add `and mimeType = 'application/vnd.google-apps.document'` (Sheets:
  `application/vnd.google-apps.spreadsheet`; Slides: `application/vnd.google-apps.presentation`)
- recent: add `and modifiedTime > '2026-10-01T00:00:00'`

Give the user the `webViewLink` so they can open the file themselves.

### Read

gws only saves files inside the current folder, so run these from `~/brain` and keep the output
path relative. The file in `inbox/` is a scratch copy; overwrite it each time.

- Google Doc or Slides as text:
  `gws drive files export --params '{"fileId":"FILE_ID","mimeType":"text/plain"}' --output inbox/.drive-export.txt`
- Google Sheet (first tab only) as CSV:
  `gws drive files export --params '{"fileId":"FILE_ID","mimeType":"text/csv"}' --output inbox/.drive-export.csv`
- Any other file (PDF, Word):
  `gws drive files get --params '{"fileId":"FILE_ID","alt":"media"}' --output inbox/.drive-file.pdf`
  (use the file's own ending, such as `.docx`).

Then read the saved file. Summarise with the answer first; quote only the lines that matter.
