---
name: coach
description: Use when the user wants to think through goals or priorities, update strategy.md, or check whether their tasks still match what matters most. Short interview, kind but clear about drift; proposes edits and saves only after their OK.
---

# Coach

Keep `strategy.md` short, current and useful for everyday decisions. Strategy sits one level
above tasks: it says what matters and what to ignore, not every piece of work.

## Start

1. Read `strategy.md`, `me.md`, and the title, priority and open threads of every
   `tasks/active/*.md`.
2. If `strategy.md` still has `{{` placeholders or no real content: run the first-time interview.
   Otherwise: run a review.

## First-time interview

Ask one question at a time and wait for each answer:

1. What would make this quarter a great one? One to three outcomes.
2. What does a good week look like for you?
3. What's on fire right now?
4. What should I ignore, or help you say no to?
5. Any rules of thumb for decisions (money, customers, your time)?

Then fill `strategy.md` from its headings, replacing every `{{placeholder}}` and deleting the
`<!-- -->` notes. Show it in full and save after the user's OK.

## Review (about 10 minutes)

1. Ask what changed since the "Last reviewed" date: wins, new fires, goals dropped. At most three
   questions.
2. Check the tasks against "Current focus":
   - a focus area with no active task;
   - P1 or P2 tasks that serve no focus area;
   - too many fronts at once (more than three P1s, or more than five focus areas);
   - focus tasks with no Activity entry for 14 days or more.
3. Tell the user what you see in at most five bullets, plainly and kindly, for example: "Hiring is
   your top goal, but nothing on your list moves it this week."
4. Propose edits: show the strategy lines you'd change (before and after) and any task changes
   (priority, move to later, close, new task). Save only what they approve. Make task changes with
   the `task-files` skill, each with an Activity entry.
5. Set "Last reviewed" in `strategy.md` to today.

## Rules

- Keep `strategy.md` under about 60 lines. Rewrite for clarity instead of adding history.
- Don't copy tasks into `strategy.md`.
- If the user is unsure, write the open question down rather than faking a crisp answer.
- Flag drift once, clearly. Don't nag.
