---
name: task-files
description: Use when the user asks where a piece of work stands, or to create, update, close, pause, reopen or archive a task file in tasks/. One file per outcome; the Log is append-only.
---

# Tasks

Task files live in `tasks/active/` (live), `tasks/later/` (paused) and `tasks/archive/` (done).
The format is in `tasks/TEMPLATE.md`.

## Find

- List live work: Glob `tasks/active/*.md`. Look in `tasks/later/` or `tasks/archive/` only when
  the user asks, or when nothing live matches.
- Search: Grep a keyword (a person, company or topic) across `tasks/`.
- Report on a task in three lines: the outcome, where it stands (latest Log line), the next step
  and who owns it.

## Create

1. Grep first: if a file already covers this outcome, update that one instead.
2. Copy `tasks/TEMPLATE.md` to `tasks/active/<short-name>.md`: lowercase words joined by dashes,
   for example `supplier-contract-renewal.md`.
3. Fill it in:
   - `title`; `priority`: P2 unless the user says otherwise. P1 only with a near date or a blocker,
     and their OK. `created` and `updated`: today. `due`: only a real deadline.
   - `## Outcome`: one sentence.
   - `## Next`: one to three tagged lines (`[ai]`, `[me]`, `[waiting]`).
   - `## Log`: `- YYYY-MM-DD: Created. <why, or where it came from>`.
4. Delete the template's `<!-- -->` notes.

## Update

- Change the `## Next` lines: add, retag, or remove a line when it's done.
- Add one dated line at the top of `## Log` saying what changed. Never edit or delete older lines.
- Set `updated` to today.

## Close, pause, reopen

- Done: Log `- YYYY-MM-DD: Done. <one line>`, then
  `mv tasks/active/<name>.md tasks/archive/<name>.md`. If that name is taken, add `-YYYY-MM-DD`
  before `.md`.
- Pause (the whole outcome is on hold): Log why and when to look again, then move the file to
  `tasks/later/`.
- Reopen: move it back to `tasks/active/` and add a Log line.

## Rules

- One file = one outcome. Split only when part of it could be finished on its own.
- `[waiting]` lines say who and since when:
  `- [waiting] Signed quote from the printer (since 2026-10-02)`.
- Dates are `YYYY-MM-DD` in files; in chat write them like "2 Oct".
- Never delete a task file; archive it.
- After every change, tell the user in one line what changed and in which file.
