"""Which scheduled jobs are due right now. Pure functions: no clock, no commands, no writes.

Every comparison and subtraction happens in UTC. Python compares (and subtracts) two datetimes
that share one tzinfo object by wall-clock time, which goes wrong across a daylight-saving
change; converting to UTC first avoids that.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .clock import fmt_local, parse_iso, tz
from .config import Config
from .jobs import JOB_ORDER

SLOT_JOBS = ("brief", "weekly_review")
# Window a slot job covers when it has never succeeded.
DEFAULT_WINDOW = {"brief": timedelta(hours=24), "weekly_review": timedelta(days=7)}
# After this many failed attempts since a slot, a slot job waits for the next slot.
RETRY_LIMIT = 3
# Non-ok runs that use up an attempt. "skipped" counts so a brief that keeps skipping
# doesn't re-run (and re-notify) every hour all day.
RETRY_STATUSES = ("error", "skipped")
# Ledger rows stamped further in the future than this (clock trouble) are ignored, so a bad
# timestamp can't stop a job forever.
FUTURE_TOLERANCE = timedelta(hours=1)
# Days to look back for a slot; 7 covers every weekday once, plus today.
SEARCH_DAYS = 8


@dataclass
class DueJob:
    name: str
    reason: str
    since: Optional[datetime] = None


# ---------------------------------------------------------------- time helpers


def parse_hhmm(value: Any) -> Tuple[int, int]:
    """'07:30' -> (7, 30). Raises ValueError for anything that isn't HH:MM."""
    text = str(value).strip()
    hour_text, sep, minute_text = text.partition(":")
    if not (sep and hour_text.isdigit() and minute_text.isdigit()
            and len(hour_text) <= 2 and len(minute_text) == 2):
        raise ValueError(f"not an HH:MM time: {value!r}")
    hour, minute = int(hour_text), int(minute_text)
    if hour > 23 or minute > 59:
        raise ValueError(f"not an HH:MM time: {value!r}")
    return hour, minute


def to_utc(dt: datetime) -> datetime:
    """Aware UTC copy. A naive datetime is taken as UTC (same rule as clock.parse_iso)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def local_time(day: date, hour: int, minute: int, zone) -> datetime:
    """`hour:minute` on `day` in `zone`, as a real instant.

    A wall time inside a spring-forward gap (01:30 on the last Sunday of March in London)
    becomes the instant just after the gap (02:30 BST); an ambiguous autumn wall time is its
    first occurrence.
    """
    wall = datetime(day.year, day.month, day.day, hour, minute, tzinfo=zone)
    return wall.astimezone(timezone.utc).astimezone(zone)


def latest_slot(now: datetime, hhmm: str, weekdays: Sequence[int], tz_name: str,
                search_days: int = SEARCH_DAYS) -> Optional[datetime]:
    """Most recent local `hhmm` on an allowed ISO weekday (Monday = 1) that is <= now."""
    zone = tz(tz_name)
    hour, minute = parse_hhmm(hhmm)
    allowed = {int(day) for day in weekdays}
    now_utc = to_utc(now)
    today = now_utc.astimezone(zone).date()
    for back in range(search_days + 1):
        day = today - timedelta(days=back)
        if day.isoweekday() not in allowed:
            continue
        slot = local_time(day, hour, minute, zone)
        if to_utc(slot) <= now_utc:
            return slot
    return None


def in_work_hours(config: Config, now: datetime) -> bool:
    """On a workday and within work_hours [start, end) in local time."""
    local = to_utc(now).astimezone(tz(config.timezone))
    start, end = config.work_hours
    return local.isoweekday() in config.workdays and start <= local.hour < end


def fmt_age(delta: timedelta) -> str:
    minutes = max(0, int(delta.total_seconds() // 60))
    if minutes < 90:
        return f"{minutes} min"
    hours = minutes // 60
    if hours < 48:
        return f"{hours} h"
    return f"{hours // 24} days"


# ---------------------------------------------------------------- settings


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _int_list(value: Any, low: int, high: int) -> bool:
    return isinstance(value, (list, tuple)) and all(
        isinstance(v, int) and not isinstance(v, bool) and low <= v <= high for v in value)


def sanitize(config: Config) -> Tuple[Config, List[str]]:
    """A copy of `config` with unusable scheduling values replaced by the defaults, plus a
    plain-English line per problem. A hand-edit typo must never stop the brief."""
    defaults = Config()
    fixes: Dict[str, Any] = {}
    problems: List[str] = []

    def use_default(key: str, why: str, shown: str) -> None:
        fixes[key] = getattr(defaults, key)
        problems.append(f"{key} {why}, so {shown} is used")

    try:
        if not isinstance(config.timezone, str):
            raise ValueError
        tz(config.timezone)
    except Exception:
        use_default("timezone", f"{config.timezone!r} isn't a known time zone", defaults.timezone)
    try:
        parse_hhmm(config.brief_time)
    except ValueError:
        use_default("brief_time", f"{config.brief_time!r} isn't an HH:MM time", defaults.brief_time)
    if not _int_list(config.workdays, 1, 7):
        use_default("workdays", "should be weekday numbers 1-7 (Monday = 1)", "Monday-Friday")
    hours = config.work_hours
    if not (_int_list(hours, 0, 24) and len(hours) == 2 and hours[0] < hours[1]):
        use_default("work_hours", "should be [start, end] hours with start < end", "[8, 18]")
    for key in ("slack_sync_minutes", "health_hours", "update_check_hours", "lookback_days_max"):
        value = getattr(config, key)
        if not (_is_number(value) and value > 0):
            use_default(key, "should be a positive number", str(getattr(defaults, key)))
    if not isinstance(config.weekly_review, bool):
        use_default("weekly_review", "should be true or false", "true")
    return (replace(config, **fixes) if fixes else config), problems


# ---------------------------------------------------------------- ledger view


class History:
    """What the scheduler needs from the runs ledger rows."""

    def __init__(self, rows: Iterable[Dict[str, Any]], now: datetime):
        self.rows = [row for row in rows if isinstance(row, dict)]
        self.horizon = to_utc(now) + FUTURE_TOLERANCE

    def _stamp(self, row: Dict[str, Any], key: str) -> Optional[datetime]:
        return self._instant(row.get(key))

    def _instant(self, text: Any) -> Optional[datetime]:
        value = parse_iso(text) if isinstance(text, str) else None
        if value is None:
            return None
        value = to_utc(value)
        return value if value <= self.horizon else None

    def last_ok(self, job: str) -> Optional[datetime]:
        """Finish time (UTC) of the latest ok run of `job`."""
        for row in reversed(self.rows):
            if row.get("job") == job and row.get("status") == "ok":
                finished = self._stamp(row, "finished_at")
                if finished is not None:
                    return finished
        return None

    def resume_point(self, job: str) -> Optional[datetime]:
        """Where the next window of a slot job starts (UTC), from its latest ok run: the
        window_end it recorded (the moment its data was gathered, so anything that arrived
        while it ran is in the next window); its window_start instead when it recorded
        complete=False (a source failed or the fallback was used, so that window is covered
        again); else its started_at, else its finished_at."""
        for row in reversed(self.rows):
            if row.get("job") != job or row.get("status") != "ok":
                continue
            finished = self._stamp(row, "finished_at")
            if finished is None:
                continue  # same rows as last_ok
            data = row.get("data") if isinstance(row.get("data"), dict) else {}
            keys = ("window_start", "window_end") if data.get("complete") is False else ("window_end",)
            for value in [self._instant(data.get(key)) for key in keys] + [self._stamp(row, "started_at")]:
                if value is not None:
                    return value
            return finished
        return None

    def failures_since(self, job: str, since_utc: datetime) -> int:
        count = 0
        for row in self.rows:
            if row.get("job") == job and row.get("status") in RETRY_STATUSES:
                started = self._stamp(row, "started_at")
                if started is not None and started >= since_utc:
                    count += 1
        return count


def _rows(runs: Any) -> List[Dict[str, Any]]:
    return runs.rows() if hasattr(runs, "rows") else list(runs)


# ---------------------------------------------------------------- due jobs


def window_start(config: Config, job: str, resume_utc: Optional[datetime],
                 now: datetime) -> datetime:
    """Start of the period a slot job covers: where its last ok run left off
    (History.resume_point), else the default window, never earlier than lookback_days_max
    before now (and never after now). Returned in local time."""
    now_utc = to_utc(now)
    since = resume_utc if resume_utc is not None else now_utc - DEFAULT_WINDOW.get(job, timedelta(hours=24))
    floor = now_utc - timedelta(days=config.lookback_days_max)
    return min(max(since, floor), now_utc).astimezone(tz(config.timezone))


def window_since(config: Config, runs: Any, job: str, now: datetime) -> Optional[datetime]:
    """`since` for a job run on request (`brain catchup --job brief`); None for interval jobs."""
    if job not in SLOT_JOBS:
        return None
    cfg, _ = sanitize(config)
    return window_start(cfg, job, History(_rows(runs), now).resume_point(job), now)


def _slot_job(job: str, weekdays: Sequence[int], cfg: Config, history: History,
              now: datetime, first_run_grace: Optional[timedelta] = None) -> Optional[DueJob]:
    """`first_run_grace`: when a job has never run, only a slot this recent counts
    (a fresh install on a Tuesday shouldn't produce last Monday's weekly review)."""
    slot = latest_slot(now, cfg.brief_time, weekdays, cfg.timezone)
    if slot is None:
        return None
    slot_utc = to_utc(slot)
    last = history.last_ok(job)
    if last is not None and last >= slot_utc:
        return None
    if last is None and first_run_grace is not None and to_utc(now) - slot_utc > first_run_grace:
        return None
    tries = history.failures_since(job, slot_utc)
    if tries >= RETRY_LIMIT:
        return None
    done = "never run" if last is None else f"last ok {fmt_local(last, cfg.timezone)}"
    reason = f"slot {fmt_local(slot, cfg.timezone)} ({done})"
    if tries:
        reason += f", attempt {tries + 1} of {RETRY_LIMIT}"
    # `last` (finished_at) decides whether the slot is done; the window resumes where the
    # last ok run's data left off.
    return DueJob(job, reason, window_start(cfg, job, history.resume_point(job), now))


def _interval_reason(history: History, job: str, now: datetime, every: timedelta) -> Optional[str]:
    last = history.last_ok(job)
    if last is None:
        return "never run"
    age = to_utc(now) - last
    if age >= every:
        return f"last ok {fmt_age(age)} ago (every {fmt_age(every)})"
    return None


def due_jobs(config: Config, runs: Any, now: datetime) -> List[DueJob]:
    """Jobs to run now, in JOB_ORDER. `runs` is a RunsLedger (or a list of ledger rows)."""
    cfg, _ = sanitize(config)
    history = History(_rows(runs), now)
    found: Dict[str, DueJob] = {}

    brief = _slot_job("brief", cfg.workdays, cfg, history, now)
    if brief is not None:
        found["brief"] = brief
    if cfg.weekly_review:
        weekly = _slot_job("weekly_review", [1], cfg, history, now, first_run_grace=timedelta(hours=24))
        if weekly is not None:
            found["weekly_review"] = weekly
    brief_due = "brief" in found

    reason = _interval_reason(history, "health", now, timedelta(hours=cfg.health_hours))
    if reason is None and brief_due:
        reason = "runs before the brief"
    if reason:
        found["health"] = DueJob("health", reason)

    reason = None
    if brief_due:
        reason = "runs before the brief"
    elif in_work_hours(cfg, now):
        reason = _interval_reason(history, "slack_sync", now, timedelta(minutes=cfg.slack_sync_minutes))
    if reason:
        found["slack_sync"] = DueJob("slack_sync", reason)

    reason = _interval_reason(history, "update_check", now, timedelta(hours=cfg.update_check_hours))
    if reason:
        found["update_check"] = DueJob("update_check", reason)

    return [found[name] for name in JOB_ORDER if name in found]
