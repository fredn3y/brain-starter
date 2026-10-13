"""Scheduled health check: doctor's quick checks, with any problems kept for the brief.

Exceptions are left to the catch-up runner, which records them as a failed run.
"""

from __future__ import annotations

from .base import JobContext, JobResult

PROBLEM_STATUSES = ("warn", "fail")


def run(ctx: JobContext) -> JobResult:
    from ..doctor import run_checks

    checks = run_checks(ctx.ws, ctx.config, ctx.runner, quick=True, env=ctx.env or None, now=ctx.now)
    problems = [check for check in checks if check.status in PROBLEM_STATUSES]
    if problems:
        summary = f"{len(problems)} problem" + ("" if len(problems) == 1 else "s")
    else:
        summary = "all good"
    return JobResult("ok", summary=summary, data={"problems": [
        {"name": c.name, "status": c.status, "message": c.message, "fix": c.fix} for c in problems
    ]})
