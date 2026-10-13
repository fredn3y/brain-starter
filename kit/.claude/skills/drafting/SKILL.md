---
name: drafting
description: Use when writing anything the user will send - an email, Slack message, reply, chase or announcement - so it sounds like them. Shows the full send-ready draft; they send it themselves.
---

# Drafting

Write it the way the user would, so they can send it with little or no editing.

## Before you write

1. **Who is it for?** Look them up in `people.md`: role, relationship, tone.
2. **What's the point?** One or two clear asks or messages. If there are more, suggest splitting.
3. **Check the facts.** Read the thread you're replying to (`mail` or `slack` skill), the related
   task, and the user's calendar for any date or time (`calendar-drive` skill). Never invent facts,
   numbers, dates or promises; if something is unknown, ask the user or leave a clear `[gap]`.

## Write

- Follow `voice.md`: greeting, sign-off, length, tone and phrases. Email and Slack differ.
- Lead with the ask or the news. Keep it short; cut anything the reader doesn't need.
- Only say what the reader knows or needs to know. Keep the user's private notes and reasoning out.
- Times: the user's timezone, plus the reader's if it's different ("10:00 London / 11:00 Paris").
- No filler such as "I hope this email finds you well" unless `voice.md` says the user writes that.

## Show

Show the whole draft in chat, send-ready:

- Email: `To:`, `Cc:`, `Subject:`, then the body with the user's sign-off.
- Slack: where it goes (channel, DM or thread link), then the message in a code block.

Add one line under it only if there's something the user should check ("I guessed the date: is
Friday right?"). If they ask for changes, show the whole new version again.

## After the user's OK

- Email: save it with the `mail` skill (`brain draft email`). It lands in their Gmail Drafts.
- Slack: they copy the text and send it.
- If it makes a promise or changes the state of a task, add a Log line there (`task-files` skill).

The user always sends. Never claim something was sent.
