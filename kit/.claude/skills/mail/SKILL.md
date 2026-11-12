---
name: mail
description: Use when the user asks about their email - what's new, what needs a reply, finding or reading a message or thread, summarising - or wants a reply or a new email drafted into Gmail. Read and draft only; they send from Gmail.
---

# Mail

Needs Google. If you don't know it's connected, run `brain google status`. Not connected: tell
the user in one line and offer to connect it (`onboard` skill, `google` part). Meanwhile, work
from mail they paste into the chat, and show drafts in chat for them to copy.

You can read Gmail and save drafts. You can't send, archive, label or delete, and you never try.

## Search

```bash
gws gmail +triage --format json --max 20 --query "<gmail search>"
```

Without `--query` it lists unread mail. It returns `messages`, each with `id`, `from`, `subject`
and `date`. Useful searches:

| Want | Query |
|---|---|
| unread that matters | `is:unread -category:promotions -category:social` |
| from one person, last week | `from:name@company.com newer_than:7d` |
| a topic | `subject:"contract" newer_than:30d` |
| mail to the user, last 2 days | `to:me newer_than:2d -from:me` |
| with attachments | `has:attachment newer_than:14d` |
| what the user sent (promises) | `in:sent newer_than:7d` |

## Read

- One message: `gws gmail +read --id MESSAGE_ID --headers --format json`. The JSON has `from`,
  `to`, `cc`, `subject`, `date`, `body_text` and `thread_id`.
- A whole thread: take `thread_id` from one message, list the thread's messages with
  `gws gmail users threads get --params '{"userId":"me","id":"THREAD_ID","format":"metadata"}'`,
  then read each message id with `+read`, oldest first.

## Summarise

- Start with what the user needs to do, or "nothing needed from you".
- Then who said what, one line each, newest first, dates in the user's timezone.
- Say why it matters: who the person is (`people.md`) and what it touches (`strategy.md`, tasks).
- Never repeat passwords, login codes or bank details from a message.
- Something actionable? Offer to add it to a task (`task-files` skill).

## Draft

1. Write it with the `drafting` skill: the user's voice, one or two clear asks.
2. Show the full draft in chat, exactly as it will be saved:

   ```text
   To: name@company.com
   Cc: (none)
   Subject: Re: Q4 budget

   <body, including the user's sign-off>
   ```

3. Wait for the user's OK. If anything changes, show the whole draft again.
4. Write the approved body, unchanged, to `inbox/.draft.txt`, then save the draft:
   - new email: `brain draft email --to ADDR --subject "SUBJECT" --body-file inbox/.draft.txt`
     (add `--cc ADDR` if needed)
   - reply: add `--reply-to MESSAGE_ID`, using the `id` of the other person's latest message
     (not one of the user's)
5. Check the command's output: To, Cc and Subject must match what the user approved. Then tell
   them: "It's in your Gmail Drafts. Open it, check it and press Send."

Put the user's sign-off in the body (see `voice.md`); don't count on Gmail adding their signature.
Never send, even if they ask. Say: "I can't send from here. It's in your Drafts, ready for you."
