"""Shared shapes for scheduled jobs."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, Optional

from ..config import Config
from ..paths import Workspace
from ..runner import Runner
from ..runs import RunsLedger

Notify = Callable[[str, str], None]


@dataclass
class JobResult:
    status: str  # "ok" | "error" | "skipped"
    summary: str = ""
    detail: Optional[str] = None
    data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class JobContext:
    ws: Workspace
    config: Config
    runner: Runner
    now: datetime
    runs: RunsLedger
    log: logging.Logger
    notify: Notify
    env: Dict[str, str] = field(default_factory=dict)
    # brief/weekly: start of the window this run covers (last ok run, capped)
    since: Optional[datetime] = None
    # results of jobs already run in this catch-up, by job name
    results: Dict[str, JobResult] = field(default_factory=dict)
