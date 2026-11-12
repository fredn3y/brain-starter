---
name: weekly-review
description: Use when the scheduled Monday job asks for the weekly review from a weekly packet, or when the user asks for a look back at last week and a plan for this one. Writes briefs/weekly-YYYY-Www.md and proposes task changes without making them.
---

# Weekly review

## Scheduled run (no shell)

You can Read, Glob and Grep, and write one file. The prompt gives the weekly packet path and the
output path (`briefs/weekly-YYYY-Www.md`, ISO week). Write only that file. Don't edit tasks:
list the changes you propose and the user approves them in a chat.

### Read

1. The packet (the window, calendar for the coming week, health and update info, failed sources).
2. `strategy.md`, `me.md`, `people.md`.
3. `tasks/active/*.md` and `tasks/later/*.md`; in `tasks/archive/`, only files whose Activity has
   a line inside the window.
4. Last week's daily briefs in `briefs/` (Glob `briefs/YYYY-MM-*.md` for the window's dates).

### Write

Markdown, at most about 450 words, in this shape:

```markdown
# Week 42 review (13-19 October)

## What moved
- Office move: van booked and keys arranged (done Thu).

## What stalled
- Q4 budget: waiting on the head of finance since 2 Oct. Smallest next step: a short nudge.

## Top 3 this week
1. Close the Q4 budget: it blocks hiring (strategy focus 1). First step: agree the numbers Wed.

## Out of step with strategy
- Most of last week went to the website refresh, which isn't a current focus.

## Proposed task changes
1. Archive "Office move": done.
2. New task: "Hire a sales lead", P2. First for you: approve the job ad.

Reply in a brain chat with the numbers you want, for example "do 1 and 2".
```

- **What moved** (max 7): things finished, decided or unblocked, from task Activity and the
  archive.
- **What stalled** (max 5): open `[human]` or `[external]` threads in tasks with no Activity entry
  for 7 days or more, and cards unanswered for three working days. Say who or what is stuck and
  the smallest next step.
- **Top 3 this week**: measured against "Current focus" in `strategy.md`. Each one: the outcome,
  why now, the first step. If the week's calendar leaves no room for them, say so.
- **Out of step with strategy** (optional, max 3): busy work outside the focus, or a focus area
  with no task at all.
- **Proposed task changes**: a numbered list, one line each with the reason: archive, move to
  later, new task, change priority or due date.
- If a source failed (see the packet), say so in one line at the top.

### Rules

- Never invent facts. Every line must trace to a task, brief, packet item or `strategy.md`.
- Dates in the user's timezone, written like "2 Oct".
- Plain words: no tags, field names or file paths in the review.

## In a chat

If this week's file exists in `briefs/`, show it; otherwise run `brain catchup --job weekly_review`
and show the new file. When the user picks changes ("do 1 and 3"), make exactly those with the
`task-files` skill and tell them what changed.
