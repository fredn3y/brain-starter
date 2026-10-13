"""The user's settings in `.kit/config.json` (created from the template, never overwritten by updates)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from typing import List

from .paths import Workspace

DEFAULT_REPO = "fredn3y/brain-starter"


@dataclass
class Config:
    timezone: str = "Europe/London"
    workdays: List[int] = field(default_factory=lambda: [1, 2, 3, 4, 5])
    brief_time: str = "07:30"
    work_hours: List[int] = field(default_factory=lambda: [8, 18])
    slack_sync_minutes: int = 60
    health_hours: int = 6
    update_check_hours: int = 24
    weekly_review: bool = True
    brief_gmail_draft: bool = True
    engine: str = "claude"
    brief_model: str = "sonnet"
    kit_repo: str = DEFAULT_REPO
    lookback_days_max: int = 14

    @property
    def brief_hour_minute(self):
        hour, minute = self.brief_time.split(":", 1)
        return int(hour), int(minute)

    def to_dict(self) -> dict:
        return asdict(self)


def load(ws: Workspace) -> Config:
    """Defaults, overlaid with any known keys from the file. Unknown keys are ignored."""
    cfg = Config()
    try:
        raw = json.loads(ws.config_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return cfg
    known = {f.name for f in fields(Config)}
    for key, value in (raw or {}).items():
        if key in known:
            setattr(cfg, key, value)
    return cfg
