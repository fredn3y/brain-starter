"""Scheduled job: the morning brief (see brief.py). catchup.py sends the "ready" notification."""

from __future__ import annotations

from .base import JobContext, JobResult


def run(ctx: JobContext) -> JobResult:
    from ..brief import run_brief

    return run_brief(ctx)
