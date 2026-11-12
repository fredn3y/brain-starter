# Task file format

Every piece of work the assistant tracks is one Markdown file in `~/brain/tasks/`. The file holds
the whole state of that outcome: what done looks like, the facts settled so far, who moves each
open thread, and a dated history. This page is the specification. The kit's `task-files` skill
is how the assistant applies it, and `tasks/TEMPLATE.md` is the blank file it starts from.

## The principle

The task file is the state. If it isn't in the file, the next session doesn't know it.

Summaries are produced when asked, from the files, and never stored. A list of what needs the
user, a weekly look back or a status line is worked out fresh each time, so it can't drift away
from the files it came from.

## Folders

The folder is the state of the outcome:

| Folder | Meaning |
|---|---|
| `tasks/active/` | live work |
| `tasks/later/` | the whole outcome is paused on purpose |
| `tasks/archive/` | done, or dropped with a reason |

- One file per outcome. Split a file only when part of it could finish on its own.
- File names are short, lowercase words joined by dashes: `march-offsite.md`. The name stays the
  same when the file moves. If the name is taken in `tasks/archive/`, add `-YYYY-MM-DD` before
  `.md`.
- Move a file to `tasks/later/` only when the whole outcome is paused. One stuck thread is not a
  pause. A paused file stays paused until the user, or a real event, brings it back.
- Task files are never deleted. They are archived.

## File shape

```markdown
---
title: "March team offsite"
priority: "P2"
created_at: "2026-02-03"
updated_at: "2026-02-10"
due_at: "2026-03-12"
completed_at: ""
links: []
---

## Outcome

Thirty people spend 12 March together somewhere good, inside the £2,500 budget.

## Pinned

- The date is 12 March. The team voted for it. (2026-02-05)
- The budget is £2,500 including food. (2026-02-03)

## Threads / Open Loops

- [human] Q: Which venue should we book?
  Context: Two places can take 30 people on 12 March. The riverside hotel is £2,400 with food.
  The barn is £1,900 and we arrange food ourselves. Both hold the date until 20 Feb.
  Options: The hotel :: food sorted, £100 under budget | The barn :: £500 cheaper, we organise food | Wait :: both holds lapse on 20 Feb
  Recommend: The hotel: it fits the budget and saves a day of organising.
  Source: both quotes, saved in inbox/ on 2026-02-10
- [ai] Draft the booking email to whichever venue is chosen.
- [external] The finance lead confirms the budget code.
  Follow-Up: 2026-02-13
- Deferred (on venue booked; review by 2026-02-24): book the coach for the day.

## Activity

### 2026-02-10 14:05 UTC
- Both quotes in. Asked which venue to book.

### 2026-02-05 09:30 UTC
- Date set: 12 March.

### 2026-02-03 16:40 UTC
- Created. From a chat about planning the offsite.
```

Front matter fields. Every value is quoted, dates are `YYYY-MM-DD`, empty is `""`:

| Field | What it holds |
|---|---|
| `title` | a short name for the outcome |
| `priority` | `"P1"`, `"P2"` or `"P3"` (see Priority) |
| `created_at` | the day the file was made |
| `updated_at` | the day of the last change; set on every edit |
| `due_at` | a real deadline, or `""` when there isn't one |
| `completed_at` | the day it was archived, otherwise `""` |
| `links` | related web links or files, for example `["https://example.com/quote", "inbox/quote.pdf"]` |

The four sections come in this order and are always present, even when empty.

## Outcome

One or two sentences: what done looks like, in words the user would use. It changes only when the
goal itself changes, and that change gets an Activity entry.

## Pinned

Settled facts the next session must not get wrong: a decision, a number, a date, a rule someone
set. Each ends with the date it was settled, in brackets:

```markdown
- The date is 12 March. The team voted for it. (2026-02-05)
```

When a newer decision supersedes a pinned fact, correct or remove the pin in the same edit that
records the decision, and say what changed in Activity. Pinned is always current; Activity keeps
the history. Keep it short: a fact that matters only once belongs in Activity.

## Threads and owners

Each open loop is one top-level bullet under `## Threads / Open Loops`, tagged with who moves it
next:

| Thread | Meaning |
|---|---|
| `- [ai] ...` | ready, concrete work for the assistant. At most about three per file. |
| `- [ai] From YYYY-MM-DD: ...` | assistant work that is not ready until that date |
| `- [human] ...` | something only the user can do or decide (a card, below) |
| `- [external] ...` | waiting on someone else. Names who and what, with a `Follow-Up:` line. |
| `- Deferred (on <trigger>; review by YYYY-MM-DD): ...` | future work nobody owns yet |

- A `[human]` thread is one of three kinds: a decision written `Q: ...?`, an approval written
  `Approve ...?`, or a plain action ("Sign the venue contract").
- An `[external]` thread always has an indented follow-up date:
  ```markdown
  - [external] The printer sends the proof.
    Follow-Up: 2026-02-14
  ```
- A Deferred thread names the event that brings it back and a date to look at it again, whichever
  comes first.
- When a thread is done, remove it and add an Activity entry saying what closed it. There are no
  ticked boxes: the Activity keeps the record.
- If there is nothing open, the section stays, empty.

## Cards

A `[human]` thread is a card: one thing only the user can settle. The user should be able to
decide from the card alone, without opening anything else. Indented under it:

| Line | When | What it holds |
|---|---|---|
| `Context:` | always | what this is, why now, and what changes with the answer |
| `Options:` | when there is a real choice | up to four `Label :: consequence`, separated by ` \| `, the helpful default first |
| `Recommend:` | on every question and approval | the suggested answer and the reason in a few words |
| `Source:` | when facts came from somewhere | the message, file or conversation, with its date |
| `Draft:` | approvals of a message | the draft itself, or where it is saved |
| `To:` | approvals of a message | who it goes to |
| `Channel:` | approvals of a message | email, Slack, phone and so on |
| `Snooze-Until:` | when the user says "not yet" | `YYYY-MM-DD`: the card stays hidden until then |

An approval card for a message:

```markdown
- [human] Approve the booking email to the hotel?
  Context: Confirms 12 March for 30 people at £2,400. Their hold ends on 20 Feb.
  Recommend: Yes, as drafted.
  Draft: Gmail draft "Booking for 12 March"
  To: the hotel's events team
  Channel: email
```

- One card, one decision. A card that would need two answers is two cards.
- If a new question overlaps an open card, rewrite that card instead of adding a second.
- A card that someone else could settle is not a card: make it `[ai]` or `[external]`.

## Activity

The dated journal of the outcome, newest first. Each entry is a heading with the date and time,
then one bullet per thing that happened:

```markdown
### 2026-02-10 14:05 UTC
- Both quotes in. Asked which venue to book.
```

- The time is in UTC (world clock time), so a file reads the same wherever it is opened.
- Append only. A new entry goes at the top. Never edit, merge, compress, summarise away or
  delete an old entry. A mistake is corrected by a new entry.
- Long is fine. The first entry ever written says `Created.` and where the work came from.
- Every change to the file earns an entry: a decision, an answer, a thread opened or closed, a
  move between folders.

## Writing things back

- **The threshold.** If the next session would be worse without it, write it back now: a
  decision, an answer, a promise, a date, a new wait. Then re-read the file to confirm the change
  landed.
- **One edit, all of it.** A change updates the threads, any Pinned fact it supersedes, an
  Activity entry and `updated_at` together.
- **Anything the user must decide or approve becomes a card** in the file, with context and a
  suggested answer. It is never left only in chat.
- **An answer settles every card it answers.** Recording the user's answer closes or rewrites
  every card it settles, in any task file, in the same edit, with a dated Activity entry in each.
- **Close only on evidence.** A card or wait closes on the user's answer, a message, a file or
  another real event, named in the Activity entry. Never because it looks old.
- **Nothing consequential disappears.** Anything tidied away (a closed card, an archived file, a
  dropped thread) is said out loud to the user.
- **In chat, plain words.** The user hears "needs you", "waiting on the printer", "parked until
  Monday". Tags, field names and file paths stay in the files unless the user asks. The user
  never has to type a tag.

## Follow-up dates, snoozing and expiry

Every wait has a date, so nothing waits forever by accident.

- `[external]` threads have a `Follow-Up:` date. On that date the wait comes back as a question
  ("still waiting on the printer?"), or the assistant checks a connected source. The answer moves
  the date on or closes the thread.
- If nobody set a follow-up date, the assistant suggests one when it writes the thread.
- `[ai] From YYYY-MM-DD:` work is not ready until its date.
- Deferred threads come back on their trigger or their review date, whichever is first.
- `Snooze-Until:` hides a card until that date. Snoozed is not gone: on the date it is back.
- **Expiry.** A card left unanswered for three working days is rechecked, never silently
  dropped. Its age counts from the Activity entry that raised it, or from its last snooze date.
  Working days are the user's own, from their settings. Rechecking means: is it still needed, are
  the facts still true, was it answered somewhere else? Then the card is refreshed (new Context
  and an Activity entry) or closed on evidence, and the user is told either way.

## Priority

- `P2` is the default.
- `P1` is rare: it needs a real date in `due_at` or a named blocker, and the user's OK. Keep about
  three P1s at most; if everything is urgent, nothing is.
- `P3` is nice to have: the first thing to let go when the week is full.

## Earlier files

Files from versions before 0.3.0 used `## Next` and `## Log`. The update converts them to this
shape once and keeps a copy of each old file.

## What later steps will add

These are planned, not part of the kit yet:

- a scheduled morning conversation that walks the user through what needs them, built on these
  files (asking "what needs me?" in a chat is its first step);
- a checker that reports files that break this format;
- automatic tidy-ups, such as rechecking expired cards and raising follow-ups that are due, always
  said out loud.
