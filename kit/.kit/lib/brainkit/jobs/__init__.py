"""Job registry. Each job module exposes `run(ctx: JobContext) -> JobResult`.

Order matters: the catch-up runner runs due jobs in this order.
"""

from __future__ import annotations

import importlib
from typing import Callable

from .base import JobContext, JobResult

JOB_ORDER = ["health", "slack_sync", "update_check", "brief", "weekly_review"]


def job_runner(name: str) -> Callable[[JobContext], JobResult]:
    module = importlib.import_module(f"{__name__}.{name}")
    return module.run  # type: ignore[attr-defined]
