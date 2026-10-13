"""The plain fallback brief, rendered straight from the data packet when the AI can't write one.

Sections: Problems, Today (and Tomorrow), Mail, Slack, Tasks. Plain Markdown, no AI involved.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

from . import clock

MAIL_LINES = 15
SLACK_LINES = 20


def _when(value: Any, tz_name: str) -> str:
    text = str(value or "")
    if text.endswith("Z"):  # Python 3.9's fromisoformat doesn't take "Z"
        text = text[:-1] + "+00:00"
    parsed = clock.parse_iso(text) if "T" in text else None
    return parsed.astimezone(clock.tz(tz_name)).strftime("%H:%M") if parsed else ""


def _one_line(text: Any, limit: int = 140) -> str:
    flat = re.sub(r"\s+", " ", str(text or "")).strip()
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"


def _events(events: Any, tz_name: str) -> List[str]:
    lines = []
    for event in events or []:
        start, end = _when(event.get("start"), tz_name), _when(event.get("end"), tz_name)
        time_text = f"{start}–{end}" if start and end else start or "all day"
        place = f" ({event['location']})" if event.get("location") else ""
        lines.append(f"- {time_text} {event.get('summary') or '(no title)'}{place}")
    return lines or ["- Nothing scheduled."]


def _problems(packet: Dict[str, Any]) -> List[str]:
    lines = []
    for item in packet.get("job_errors") or []:
        times = f" ({item['count']} times)" if (item.get("count") or 0) > 1 else ""
        lines.append(f"- {item.get('job')} failed{times}: {_one_line(item.get('detail') or 'no details')}")
    for item in (packet.get("health") or {}).get("problems") or []:
        fix = f" — {item['fix']}" if item.get("fix") else ""
        lines.append(f"- {item.get('name')}: {item.get('message')}{fix}")
    for item in packet.get("errors") or []:
        lines.append(f"- Couldn't load {item.get('source')}: {item.get('error')}")
    update = packet.get("update") or {}
    if update.get("update_available"):
        lines.append(f"- A kit update is available (v{update.get('latest')}): run `brain update`")
    return lines or ["- Nothing to report."]


def _since(packet: Dict[str, Any], tz_name: str) -> str:
    since = clock.parse_iso((packet.get("window") or {}).get("since"))
    if since is None:
        return ""
    local = since.astimezone(clock.tz(tz_name))
    return f" since {local.strftime('%a')} {local.day} {local.strftime('%b %H:%M')}"


def _mail(packet: Dict[str, Any], tz_name: str) -> List[str]:
    mail = packet.get("mail") or {}
    messages = mail.get("messages") or []
    shown = messages[:MAIL_LINES]
    lines = [f"- {_one_line(m.get('from'), 60)} — {_one_line(m.get('subject'), 90)} <{m.get('link')}>"
             for m in shown]
    total = mail.get("estimated_total")
    if isinstance(total, int) and total > len(shown):
        lines.append(f"- Showing the newest {len(shown)} of about {total} emails{_since(packet, tz_name)}.")
    return lines or ["- No new mail."]


def _slack(packet: Dict[str, Any], tz_name: str) -> List[str]:
    lines = []
    for row in (packet.get("slack") or [])[:SLACK_LINES]:
        when = clock.parse_iso(row.get("time"))
        stamp = clock.fmt_local(when, tz_name) if when else ""
        lines.append(f"- {stamp} · {row.get('channel')} · {row.get('from')}: {_one_line(row.get('text'))} "
                     f"<{row.get('link')}>")
    return lines or ["- No new messages."]


def _tasks(packet: Dict[str, Any]) -> List[str]:
    lines = []
    for task in packet.get("tasks") or []:
        tags = [str(task["priority"])] if task.get("priority") else []
        tags += [f"due {task['due']}"] if task.get("due") else []
        label = f" ({', '.join(tags)})" if tags else ""
        step = f" — next: {_one_line(task['next_steps'][0], 100)}" if task.get("next_steps") else ""
        lines.append(f"- {task.get('title')}{label}{step}")
    return lines or ["- No active tasks."]


def render_fallback(packet: Dict[str, Any], reason: str, tz_name: str) -> str:
    out = [f"AI summary unavailable today: {reason}", "", "## Problems"] + _problems(packet)
    out += ["", "## Today"] + _events(packet.get("calendar_today"), tz_name)
    out += ["", "### Tomorrow"] + _events(packet.get("calendar_tomorrow"), tz_name)
    out += ["", "## Mail"] + _mail(packet, tz_name)
    out += ["", "## Slack"] + _slack(packet, tz_name)
    out += ["", "## Tasks"] + _tasks(packet)
    return "\n".join(out) + "\n"
