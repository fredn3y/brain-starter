"""Scheduled job: the Monday weekly review (shared helpers live in brief.py).

Packet briefs/.packets/weekly-<YYYY-Www>.json: window {since, now, tz}; briefs (the daily
briefs in the window); tasks (active, as in the brief packet); archived_this_week [{path,
title}]; job_errors [{job, count, detail, last}] for the window; files {me, strategy,
previous_review}; errors. The `weekly-review` skill writes briefs/weekly-<YYYY-Www>.md; if it
doesn't, a short plain list is written instead. No Gmail draft. The week label is the ISO week
of the middle of the window, so a Monday run names the week it reviews.
"""

from __future__ import annotations

import re
import time
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from ..clock import parse_iso, to_iso, tz
from .base import JobContext, JobResult

WINDOW = timedelta(days=7)
SKILL = "weekly-review"
WEEKLY_NAME = re.compile(r"^weekly-(\d{4})-W(\d{2})(?:-(\d+))?\.md$")


def week_label(since: datetime, now: datetime, tz_name: str) -> str:
    middle = (since + (now - since) / 2).astimezone(tz(tz_name))
    year, week, _ = middle.isocalendar()
    return f"{year}-W{int(week):02d}"


def week_briefs(ctx: JobContext, since: datetime) -> List[str]:
    from ..brief import BRIEF_NAME

    zone = tz(ctx.config.timezone)
    first, last = since.astimezone(zone).date(), ctx.now.astimezone(zone).date()
    found = []
    for path in sorted(ctx.ws.briefs.glob("*.md")):
        match = BRIEF_NAME.match(path.name)
        if match and first <= date.fromisoformat(match.group(1)) <= last:
            found.append(f"briefs/{path.name}")
    return found


def archived_this_week(ctx: JobContext, since: datetime) -> List[Dict[str, Any]]:
    from ..brief import parse_task

    done = []
    for path in sorted((ctx.ws.tasks / "archive").glob("*.md")):
        try:
            if path.stat().st_mtime >= since.timestamp():
                task = parse_task(path, ctx.ws.root)
                done.append({"path": task["path"], "title": task["title"]})
        except OSError:
            continue
    return done


def week_errors(ctx: JobContext, since: datetime) -> List[Dict[str, Any]]:
    errors: Dict[str, Dict[str, Any]] = {}
    for row in ctx.runs.rows():
        started = parse_iso(row.get("started_at"))
        job = row.get("job")
        if row.get("status") != "error" or not job or job == "catchup" or started is None:
            continue
        if started.timestamp() < since.timestamp():
            continue
        entry = errors.setdefault(job, {"job": job, "count": 0, "detail": None, "last": None})
        entry.update(count=entry["count"] + 1, detail=row.get("detail") or row.get("summary"),
                     last=row.get("finished_at"))
    return list(errors.values())


def previous_review(ctx: JobContext, exclude_stem: str) -> Optional[str]:
    best = None
    for path in ctx.ws.briefs.glob("weekly-*.md"):
        match = WEEKLY_NAME.match(path.name)
        if not match or path.stem == exclude_stem:
            continue
        key = (int(match.group(1)), int(match.group(2)), int(match.group(3) or 1))
        if best is None or key > best[0]:
            best = (key, path.name)
    return f"briefs/{best[1]}" if best else None


def build_packet(ctx: JobContext, stem: str, since: datetime) -> Dict[str, Any]:
    from ..brief import collect_tasks, gather, write_packet

    packet: Dict[str, Any] = {"window": {"since": to_iso(since), "now": to_iso(ctx.now),
                                         "tz": ctx.config.timezone}, "errors": []}
    gather(packet, "briefs", lambda: week_briefs(ctx, since), [])
    gather(packet, "tasks", lambda: collect_tasks(ctx.ws), [])
    gather(packet, "archived_this_week", lambda: archived_this_week(ctx, since), [])
    gather(packet, "job_errors", lambda: week_errors(ctx, since), [])

    def files() -> Dict[str, str]:
        out = {name: f"{name}.md" for name in ("me", "strategy") if (ctx.ws.root / f"{name}.md").exists()}
        previous = previous_review(ctx, stem)
        if previous:
            out["previous_review"] = previous
        return out

    gather(packet, "files", files, {})
    write_packet(ctx.ws, stem, packet)
    return packet


def render_fallback(packet: Dict[str, Any], reason: str) -> str:
    out = [f"AI summary unavailable this week: {reason}", "", "## Briefs this week"]
    out += [f"- {path}" for path in packet.get("briefs") or []] or ["- None."]
    out += ["", "## Done this week"]
    out += [f"- {t.get('title')} ({t.get('path')})" for t in packet.get("archived_this_week") or []] or ["- None."]
    out += ["", "## Open tasks"]
    for task in packet.get("tasks") or []:
        step = f" — next: {task['next_steps'][0]}" if task.get("next_steps") else ""
        due = f" (due {task['due']})" if task.get("due") else ""
        out.append(f"- {task.get('title')}{due}{step}")
    out += [] if packet.get("tasks") else ["- No active tasks."]
    out += ["", "## Problems this week"]
    out += [f"- {e.get('job')} failed {e.get('count')} time(s): {e.get('detail') or 'no details'}"
            for e in packet.get("job_errors") or []] or ["- None."]
    out += [f"- Couldn't load {e.get('source')}: {e.get('error')}" for e in packet.get("errors") or []]
    return "\n".join(out) + "\n"


def run(ctx: JobContext) -> JobResult:
    from .. import engine
    from ..brief import day_label, ensure_stamp, free_stem, fresh_output, stamp_line, write_private

    tz_name = ctx.config.timezone
    since = ctx.since or (ctx.now - WINDOW)
    stem = free_stem(ctx.ws.briefs, f"weekly-{week_label(since, ctx.now, tz_name)}")
    target = ctx.ws.briefs / f"{stem}.md"
    started = time.time()
    packet = build_packet(ctx, stem, since)
    args_text = (f"Data packet: briefs/.packets/{stem}.json\nWrite the review to: briefs/{stem}.md\n"
                 f"It covers {day_label(since, tz_name)} to {day_label(ctx.now, tz_name)}.")
    result = engine.run_skill(ctx, SKILL, args_text, engine.DEFAULT_TOOLS)
    data: Dict[str, Any] = {"path": f"briefs/{stem}.md", "fallback": False, "cost_usd": result.cost_usd}
    if fresh_output(target, started):
        text = target.read_text(encoding="utf-8", errors="replace")
    else:
        reason = result.reason or "the AI didn't write the review"
        data.update(fallback=True, engine_error=reason)
        text = render_fallback(packet, reason)
        ctx.log.warning("weekly review: AI summary unavailable (%s); wrote the fallback", reason)
    write_private(target, ensure_stamp(text, stamp_line(ctx.now, since, tz_name)))
    # Same window bookkeeping as the brief: a fallback review is covered again next week.
    data.update(window_start=to_iso(since), window_end=packet["window"]["now"], complete=not data["fallback"])
    summary = "fallback weekly review (AI unavailable)" if data["fallback"] else "weekly review ready"
    return JobResult("ok", summary, data=data)
