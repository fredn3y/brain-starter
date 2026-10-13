"""Scheduled job: fetch new Slack messages into ledgers/slack/ (see slack_sync.py).

Skipped (not an error) until a Slack token is saved in the Keychain.
"""

from __future__ import annotations

from .base import JobContext, JobResult

DATA_KEYS = ("conversations", "messages", "dms", "mentions", "errors")


def run(ctx: JobContext) -> JobResult:
    from .. import secrets, slack_sync
    from ..slack_api import SlackError

    try:
        token = secrets.get(ctx.runner, "slack")
    except secrets.SecretError as exc:
        return JobResult("error", "couldn't read the Slack token", detail=str(exc))
    if not token:
        return JobResult("skipped", "Slack not connected yet")
    try:
        summary = slack_sync.sync(ctx.ws, ctx.runner, token, ctx.now, ctx.log)
    except SlackError as exc:
        return JobResult("error", "Slack sync failed", detail=str(exc))
    data = {key: int(summary.get(key, 0)) for key in DATA_KEYS}
    if data["conversations"] and data["errors"] >= data["conversations"]:
        return JobResult("error", "Slack sync failed for every conversation",
                         detail="see logs/brain.log for the Slack error codes", data=data)
    return JobResult("ok", slack_sync.summary_text(summary), data=data)
