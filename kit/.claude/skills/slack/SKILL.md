---
name: slack
description: Use when the user asks what's happening in Slack, what needs them there, to find or summarise a conversation or thread, or to draft a Slack reply. Read only - replies are drafted in chat for them to paste and send.
---

# Slack

Needs Slack. If you don't know it's connected, run `brain slack whoami`. Not connected: tell the
user in one line and offer to connect it (`onboard` skill, `slack` part). Meanwhile, work from
messages they paste into the chat.

Read only. The brain can't post to Slack: show replies in chat; the user pastes and sends them.

## Commands

| Need | Command |
|---|---|
| recent messages | `brain slack recent --hours 24 --json` |
| only direct messages / only @-mentions of the user | add `--dms` / `--mentions` |
| search | `brain slack search "QUERY" --count 20` |
| a whole thread | `brain slack thread CHANNEL TS` (both come from recent or search results) |
| channels the user is in | `brain slack channels` |
| fetch the latest now | `brain slack sync` (hourly in work hours once the morning brief is on) |
| go further back | `brain slack sync --lookback-days 14` |
| check the connection | `brain slack whoami` |

Search words work as in Slack's search box, for example `in:#sales`, `from:@name`,
`after:2026-10-01`.

If a command says Slack isn't connected or a permission is missing, use the `troubleshoot` skill.

## Summarise

- What needs the user first: questions to them, decisions, @-mentions, DMs waiting on a reply.
- Then the rest: who, where (#channel or DM), what, one line each, with the link.
- For a thread: the decision or answer first, then what's still open.
- Weigh it with `people.md` (who matters) and `strategy.md` (what matters).
- Something actionable? Offer to add it to a task (`task-files` skill).

## Draft a reply

1. Read the thread first (`brain slack thread …`) so the reply fits what was actually said.
2. Write it in the user's Slack voice (`voice.md`): usually shorter and less formal than email.
   One or two clear asks. Don't mention private context the other person doesn't have.
3. Show it in a code block, with where it goes:

   ```text
   Reply in thread: <link>
   ---
   <the message>
   ```

4. The user copies it into Slack and sends it. Never say it was sent. If a reply makes a promise or
   names a date, offer to log it in the related task.
