"""Time helpers. All stored times are ISO-8601 with an offset; display is local time."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

try:  # Python 3.9+ has zoneinfo; macOS ships tz data in /usr/share/zoneinfo
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - only on very old Pythons
    ZoneInfo = None  # type: ignore[assignment]


def tz(name: str):
    if ZoneInfo is None:
        return timezone.utc
    return ZoneInfo(name)


def now(tz_name: str = "Europe/London") -> datetime:
    return datetime.now(tz(tz_name))


def to_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        raise ValueError("naive datetime")
    return dt.isoformat(timespec="seconds")


def parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def fmt_local(dt: datetime, tz_name: str, with_day: bool = True) -> str:
    """'Thu 17:40' style, or '17:40' when with_day is False."""
    local = dt.astimezone(tz(tz_name))
    return local.strftime("%a %H:%M") if with_day else local.strftime("%H:%M")
