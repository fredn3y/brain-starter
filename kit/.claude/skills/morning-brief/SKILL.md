---
name: morning-brief
description: Use when the scheduled brief job asks for today's morning brief from a data packet, or when the user asks for their brief in a chat. The scheduled run reads the packet and their files and writes one Markdown file, nothing else.
---

# Morning brief

## Scheduled run (no shell)

You can Read, Glob and Grep, and write one file. The prompt gives two paths: the packet (JSON)
and the output file. Write only that output file. Never edit tasks or any other file; put
proposed changes under Suggestions.

### Read, in this order

1. The packet.
2. `me.md`, `strategy.md`, `people.md`, `voice.md`.
3. `tasks/active/*.md` (Glob, then Read). Look in `tasks/later/` only if something points there.

### The packet

Use what it contains; never guess what's missing. It usually holds: the window (`since`, `now`,
`tz`), health problems, failed jobs, update info (`update_available`, `latest`, `notes`),
calendar events, mail, Slack messages, and the sources that failed. A source that is missing or
reports an error is a gap: say so under First. Never fill a gap from memory.

### Write

Markdown, at most about 350 words. Sections in this order; leave out empty ones except Today.

```markdown
# Tuesday 14 October
Since Thu 17:40

## First
- Slack couldn't be read (the token was rejected), so Slack is missing below. Say "fix Slack" in a brain chat.
- Update available (v0.2.0): faster briefs. Say 'update' in a brain chat to install it.

## Today
- 09:30-10:00 Weekly sales check-in (with the sales lead). Prep: the pricing decision is still open.
- 14:00-15:00 Supplier call.

## Needs you
- **Head of finance**: needs your OK on the Q4 budget by Thursday. It blocks hiring. [Open](link)

## Tasks
- Office move: confirm the van booking (due Fri).

## Suggestions
- Draft a reply to the head of finance proposing Wednesday?

## FYI
- The board pack is in Drive.
```

- **Title**: the weekday and date in the user's timezone. Add the `Since <Ddd HH:MM>` line only
  when the window is longer than a day.
- **First** (only if something is wrong or new): health problems, failed jobs and failed sources,
  one plain line each with the fix. An update, exactly: `Update available (vX.Y.Z): <one line
  from the notes>. Say 'update' in a brain chat to install it.` If `mail.estimated_total` is
  larger than the number of mail messages in the packet: `Showing the newest N of about M emails
  since <Ddd HH:MM>.`
- **Today**: every event, `HH:MM-HH:MM Title (with whom)`. Add a short prep note when a meeting
  links to a task or a person in `people.md`. No events: "No meetings today."
- **Needs you** (max 7, most important first): mail and Slack waiting on the user: a question, a
  request, a decision, a deadline. Judge importance with `people.md` (who matters) and
  `strategy.md` (what matters). Skip newsletters, notifications and anything the user has already
  answered. Each line: **who**, what they need, why it matters, and a link.
- **Tasks** (max 5): due or overdue first, then stalled ones (an open `[me]` or `[waiting]` line
  and no Log entry for 7 days), then the next `[me]` steps.
- **Suggestions** (max 3): things you could draft or do next. The user answers in a brain chat.
- **FYI** (optional, max 5, one line each).

### Rules

- Never invent facts, names, dates or links. Use each item's link from the packet. If a mail item
  has only a message id, link `https://mail.google.com/mail/?authuser=<the user's Google email>#all/<id>`;
  otherwise leave the link out.
- Times in the user's timezone. Follow "How I like to be told things" in `me.md`.
- Never copy passwords, login codes or bank details into the brief.
- Short lines, plain words. Cut FYI before cutting Needs you.

## In a chat

The brief is an add-on and needs Google. If `brain google status` says Google isn't connected,
say so in one line and offer to set it up (`onboard` skill, `google` part, then `brief`).

If today's file is in `briefs/` (`YYYY-MM-DD.md`), show it. If not, run `brain brief --detach`: it
returns at once and writes the brief in the background. After a minute or two, show the newest
file in `briefs/` for today (a second brief the same day is `YYYY-MM-DD-2.md`). Nothing new after
about 3 minutes: run `brain brief --detach` once more.
