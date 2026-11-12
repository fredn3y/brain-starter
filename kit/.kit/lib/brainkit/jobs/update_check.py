"""Scheduled job: is a newer kit release published? (Only checks; `brain update` applies.)

`check` is shared with the Claude Code session-start hook (session_start.py), so the schedule
and the hook record the same `update_check` result in the runs ledger.
"""

from __future__ import annotations

from ..http import HttpError
from ..update import ReleaseError, check_latest, installed_version, parse_version
from .base import JobContext, JobResult


def check(ws, runner, repo: str, timeout: int = 30) -> JobResult:
    current = installed_version(ws)
    try:
        release = check_latest(runner, repo, timeout=timeout)
        available = release is not None and parse_version(release.version) > parse_version(current)
    except (HttpError, ReleaseError, ValueError, OSError) as exc:
        return JobResult("error", "update check failed", detail=str(exc)[:200])
    data = {
        "current": current,
        "latest": release.version if release else None,
        "update_available": available,
        "notes": (release.notes if release else "")[:500],
        "html_url": release.html_url if release else "",
    }
    summary = f"update available: v{release.version}" if available and release else "up to date"
    return JobResult("ok", summary, data=data)


def run(ctx: JobContext) -> JobResult:
    return check(ctx.ws, ctx.runner, ctx.config.kit_repo)
