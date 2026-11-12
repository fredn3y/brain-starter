"""The morning brief: data packet → engine writes the brief → header stamp → Gmail draft.

`build_packet` gathers calendar, mail, Slack, tasks and the kit's own health into
briefs/.packets/<stem>.json. Each source is isolated: a failure becomes an entry in
packet["errors"] and the rest still arrive. The `morning-brief` skill (headless Claude Code)
writes briefs/<stem>.md; if it doesn't, brief_fallback.py renders a plain brief from the packet. The
first line is always the header stamp. catchup.py sends the "brief is ready" notification.

Packet keys (the skills read these):
  window {since, now, tz}; calendar_today, calendar_tomorrow [{calendar, start, end, location,
  summary}]; mail {query, count, messages [{id, thread_id, from, to, subject, date, excerpt,
  link}]}; slack [{time, channel, kind, from, from_me, mentions_me, in_thread, text, files, link}]
  (DMs and mentions first); slack_status; tasks [{path, title, priority, due (the due_at date),
  outcome, next_steps ("[tag] text" per open thread, a Deferred loop as written), threads [{tag
  ai|human|external|deferred, kind question|approval|deferred|thread, text, and context,
  options, recommend, follow_up, snooze_until, to, channel, draft, source when set}], last_log
  (newest Activity date)}] read by taskfile.py from either task format; health {problems,
  checked_at}; update; job_errors [{job, detail, count, last}]; files {me, strategy, people, voice, previous_brief}; errors [{source, error}].

Mail and Slack content goes only into the packet and the brief, never into logs.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import clock, gws, taskfile
from .brief_fallback import render_fallback
from .jobs.base import JobContext, JobResult
from .paths import Workspace

DEFAULT_WINDOW = timedelta(hours=24)
BRIEF_SKILL = "morning-brief"
MAIL_MAX = 40
MAIL_READ = 10
EXCERPT_CHARS = 1500
SLACK_CAP = 80
PERSON_FILES = ("me", "strategy", "people", "voice")
STAMP_PREFIX = "> Generated "
BRIEF_NAME = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:-(\d+))?\.md$")
GMAIL_LINK = "https://mail.google.com/mail/u/0/#inbox/"
WINDOW_SOURCES = ("mail", "calendar_today", "calendar_tomorrow", "slack")


# ---------------------------------------------------------------- shared helpers


def day_label(dt: datetime, tz_name: str) -> str:
    """'Tue 21 Oct 09:12' in local time."""
    local = dt.astimezone(clock.tz(tz_name))
    return f"{local.strftime('%a')} {local.day} {local.strftime('%b %H:%M')}"


def stamp_line(now: datetime, since: datetime, tz_name: str) -> str:
    return f"{STAMP_PREFIX}{day_label(now, tz_name)} · covers since {day_label(since, tz_name)}"


def ensure_stamp(text: str, stamp: str) -> str:
    """Make `stamp` the first line (replacing an older stamp, else inserted above)."""
    lines = text.splitlines()
    if lines and lines[0].startswith(STAMP_PREFIX.strip()):
        lines[0] = stamp
    else:
        lines = [stamp, ""] + lines
    return "\n".join(lines).rstrip() + "\n"


def free_stem(folder: Path, base: str) -> str:
    """`base`, else `base-2`, `base-3`, … so an existing file is never overwritten."""
    number = 1
    while True:
        stem = base if number == 1 else f"{base}-{number}"
        if not (folder / f"{stem}.md").exists():
            return stem
        number += 1


def write_private(path: Path, text: str) -> None:
    """Atomic write, readable by the user only (briefs and packets hold mail and Slack text)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with os.fdopen(os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w",
                   encoding="utf-8") as handle:
        handle.write(text)
    os.replace(str(tmp), str(path))


def write_packet(ws: Workspace, stem: str, packet: Dict[str, Any]) -> Path:
    path = ws.packets / f"{stem}.json"
    write_private(path, json.dumps(packet, indent=1, ensure_ascii=False, default=str) + "\n")
    return path


def fresh_output(path: Path, started: float) -> bool:
    """Written by this run: exists, not empty, modified after `started` (2 s for coarse clocks)."""
    try:
        info = path.stat()
    except OSError:
        return False
    return info.st_size > 0 and info.st_mtime >= started - 2


def short_error(exc: BaseException) -> str:
    text = re.sub(r"\s+", " ", f"{type(exc).__name__}: {exc}").strip()
    return text if len(text) <= 200 else text[:199] + "…"


def gws_env(ctx: JobContext) -> Optional[Dict[str, str]]:
    return dict(ctx.env) if ctx.env else None


def job_errors(ctx: JobContext, since: datetime) -> List[Dict[str, Any]]:
    """Jobs whose latest run since `since` failed: {job, detail, count, last}."""
    errors: Dict[str, Dict[str, Any]] = {}
    latest: Dict[str, Any] = {}
    for row in ctx.runs.rows():
        job = row.get("job")
        started = clock.parse_iso(row.get("started_at"))
        if not job or job == "catchup" or started is None or started.timestamp() < since.timestamp():
            continue
        latest[job] = row.get("status")
        if row.get("status") == "error":
            entry = errors.setdefault(job, {"job": job, "detail": None, "count": 0, "last": None})
            entry.update(count=entry["count"] + 1, detail=row.get("detail") or row.get("summary"),
                         last=row.get("finished_at"))
    for job, result in ctx.results.items():
        latest[job] = result.status
        if result.status == "error" and job not in errors:
            errors[job] = {"job": job, "detail": result.detail or result.summary, "count": 1, "last": None}
    return [entry for job, entry in errors.items() if latest.get(job) == "error"]


# ---------------------------------------------------------------- tasks


THREAD_FIELDS = ("context", "options", "recommend", "follow_up", "snooze_until", "to", "channel",
                 "draft", "source")


def _thread_view(thread: Dict[str, Any]) -> Dict[str, Any]:
    view = {"tag": thread["tag"], "kind": thread["kind"], "text": thread["text"][:300]}
    view.update({key: str(thread[key])[:300] for key in THREAD_FIELDS if thread.get(key)})
    return view


def parse_task(path: Path, root: Path) -> Dict[str, Any]:
    """One task file (either format) as a packet entry; see the module notes for the keys."""
    task = taskfile.read_task(path.read_text(encoding="utf-8", errors="replace"))
    meta, threads = task["meta"], task["threads"][:10]
    steps = [(t["text"] if t["tag"] == "deferred" else f"[{t['tag']}] {t['text']}")[:300] for t in threads]
    return {"path": os.path.relpath(str(path), str(root)),
            "title": meta.get("title") or task["heading"] or path.stem,
            "priority": meta.get("priority") or None, "due": meta.get("due_at") or None,
            "outcome": task["outcome"][:300], "next_steps": steps,
            "threads": [_thread_view(t) for t in threads], "last_log": task["last_activity"]}


def collect_tasks(ws: Workspace, folder: str = "active", limit: int = 60) -> List[Dict[str, Any]]:
    out = []
    for path in sorted((ws.tasks / folder).glob("*.md"))[:limit]:
        if path.name.startswith(".") or path.name.upper() == "TEMPLATE.MD":
            continue
        try:
            out.append(parse_task(path, ws.root))
        except OSError:
            continue
    return out


def previous_brief(ws: Workspace, exclude_stem: Optional[str] = None) -> Optional[Path]:
    best: Optional[Tuple[Tuple[str, int], Path]] = None
    try:
        entries = list(ws.briefs.iterdir())
    except OSError:
        return None
    for path in entries:
        match = BRIEF_NAME.match(path.name)
        if not match or path.stem == exclude_stem:
            continue
        key = (match.group(1), int(match.group(2) or 1))
        if best is None or key > best[0]:
            best = (key, path)
    return best[1] if best else None


# ---------------------------------------------------------------- packet sources


def gather(packet: Dict[str, Any], name: str, func: Callable[[], Any], default: Any = None) -> None:
    try:
        packet[name] = func()
    except Exception as exc:  # one broken source must not cost the whole brief
        packet[name] = default
        packet["errors"].append({"source": name, "error": short_error(exc)})


def _agenda(ctx: JobContext, flag: str) -> List[Dict[str, Any]]:
    data = gws.run_json(ctx.runner, ["calendar", "+agenda", flag, "--format", "json", "--timezone",
                                     ctx.config.timezone], env=gws_env(ctx))
    events = data.get("events") if isinstance(data, dict) else None
    return [event for event in events or [] if isinstance(event, dict)]


def _person(value: Any) -> str:
    if isinstance(value, dict):
        name, email = str(value.get("name") or "").strip(), str(value.get("email") or "").strip()
        return f"{name} <{email}>" if name and email else name or email
    return str(value or "")


def _mail_time(item: Dict[str, Any]) -> float:
    try:
        return parsedate_to_datetime(str(item.get("date") or "")).timestamp()
    except (TypeError, ValueError, IndexError, OverflowError):
        return 0.0


def _mail(ctx: JobContext, since: datetime) -> Dict[str, Any]:
    env = gws_env(ctx)
    query = f"in:inbox -category:promotions -category:social after:{int(since.timestamp())}"
    data = gws.run_json(ctx.runner, ["gmail", "+triage", "--format", "json", "--max", str(MAIL_MAX),
                                     "--query", query], env=env)
    found = [m for m in ((data or {}).get("messages") or []) if isinstance(m, dict) and m.get("id")]
    found.sort(key=_mail_time, reverse=True)
    messages = []
    for index, item in enumerate(found):
        entry: Dict[str, Any] = {"id": item["id"], "thread_id": None, "from": _person(item.get("from")), "to": "",
                                 "subject": str(item.get("subject") or ""), "date": str(item.get("date") or ""),
                                 "excerpt": None, "link": GMAIL_LINK + str(item["id"])}
        if index < MAIL_READ:
            try:
                full = gws.run_json(ctx.runner, ["gmail", "+read", "--id", str(item["id"]), "--headers",
                                                 "--format", "json"], env=env)
            except gws.GwsError as exc:
                entry["excerpt_error"] = str(exc)
                full = None
            if isinstance(full, dict):
                body = re.sub(r"\n{3,}", "\n\n", str(full.get("body_text") or "")).strip()
                entry.update(thread_id=full.get("thread_id"), excerpt=body[:EXCERPT_CHARS],
                             to=", ".join(_person(p) for p in full.get("to") or []))
                entry["from"] = _person(full.get("from")) or entry["from"]
        messages.append(entry)
    estimate = (data or {}).get("resultSizeEstimate")
    total = max(estimate, len(found)) if isinstance(estimate, int) and not isinstance(estimate, bool) else len(found)
    return {"query": query, "count": len(found), "estimated_total": total, "messages": messages}


def _slack(ctx: JobContext, since: datetime) -> List[Dict[str, Any]]:
    from . import slack_sync

    names = slack_sync.load_json(slack_sync.users_path(ctx.ws), {})
    first, rest = [], []
    for row in slack_sync.recent(ctx.ws, since, limit=0):
        direct = row.get("kind") in slack_sync.DM_KINDS
        if row.get("from_me") and not direct:
            continue
        (first if direct or row.get("mentions_me") else rest).append(row)
    chosen = first[-SLACK_CAP:]
    room = SLACK_CAP - len(chosen)
    if room > 0 and rest:
        chosen += rest[-room:]
    return [{"time": row.get("time"), "channel": row.get("channel_name"), "kind": row.get("kind"),
             "from": row.get("user_name"), "from_me": bool(row.get("from_me")),
             "mentions_me": bool(row.get("mentions_me")), "in_thread": bool(row.get("in_thread")),
             "text": slack_sync.readable(str(row.get("text") or ""), names),
             "files": [f.get("name") for f in row.get("files") or [] if isinstance(f, dict)],
             "link": row.get("permalink")} for row in chosen]


def _job_note(ctx: JobContext, job: str) -> Optional[Dict[str, Any]]:
    result = ctx.results.get(job)
    if result is not None:
        return {"status": result.status, "summary": result.summary}
    row = ctx.runs.last(job, status=None)
    if row is None:
        return None
    return {"status": row.get("status"), "summary": row.get("summary"), "at": row.get("finished_at")}


def _health(ctx: JobContext) -> Optional[Dict[str, Any]]:
    result = ctx.results.get("health")
    if result is not None and result.status == "ok" and "problems" in (result.data or {}):
        return {"problems": result.data["problems"], "checked_at": clock.to_iso(ctx.now)}
    row = ctx.runs.last("health")
    if row is None:
        return None
    return {"problems": (row.get("data") or {}).get("problems", []), "checked_at": row.get("finished_at")}


def _update(ctx: JobContext) -> Optional[Dict[str, Any]]:
    result = ctx.results.get("update_check")
    if result is not None and result.status == "ok":
        return dict(result.data or {})
    row = ctx.runs.last("update_check")
    return dict(row.get("data") or {}) if row else None


def _files(ws: Workspace, stem: Optional[str]) -> Dict[str, str]:
    files = {name: f"{name}.md" for name in PERSON_FILES if (ws.root / f"{name}.md").exists()}
    previous = previous_brief(ws, stem)
    if previous is not None:
        files["previous_brief"] = f"briefs/{previous.name}"
    return files


def build_packet(ctx: JobContext, stem: Optional[str] = None) -> Dict[str, Any]:
    """Gather every source and write briefs/.packets/<stem>.json (stem defaults to today's date)."""
    since = ctx.since or (ctx.now - DEFAULT_WINDOW)
    stem = stem or ctx.now.astimezone(clock.tz(ctx.config.timezone)).strftime("%Y-%m-%d")
    packet: Dict[str, Any] = {"window": {"since": clock.to_iso(since), "now": clock.to_iso(ctx.now),
                                         "tz": ctx.config.timezone}, "errors": []}
    gather(packet, "calendar_today", lambda: _agenda(ctx, "--today"), [])
    gather(packet, "calendar_tomorrow", lambda: _agenda(ctx, "--tomorrow"), [])
    gather(packet, "mail", lambda: _mail(ctx, since))
    gather(packet, "slack", lambda: _slack(ctx, since), [])
    gather(packet, "slack_status", lambda: _job_note(ctx, "slack_sync"))
    gather(packet, "tasks", lambda: collect_tasks(ctx.ws), [])
    gather(packet, "health", lambda: _health(ctx))
    gather(packet, "update", lambda: _update(ctx))
    gather(packet, "job_errors", lambda: job_errors(ctx, since), [])
    gather(packet, "files", lambda: _files(ctx.ws, stem), {})
    write_packet(ctx.ws, stem, packet)
    return packet


# ---------------------------------------------------------------- the job


def _gmail_draft(ctx: JobContext, text: str) -> Tuple[Optional[str], Optional[str]]:
    """(draft id, note). Failure is a note, never an error."""
    env = gws_env(ctx)
    try:
        status = gws.auth_status(ctx.runner, env=env)
        if not gws.has_scope(status, gws.GMAIL_COMPOSE):
            return None, "Gmail drafts permission not granted"
        if not status.get("user"):
            return None, "no Google account signed in"
        local = ctx.now.astimezone(clock.tz(ctx.config.timezone))
        subject = f"Brief · {local.strftime('%a')} {local.day} {local.strftime('%b')}"
        made = gws.create_draft(ctx.runner, str(status["user"]), subject, text, env=env)
    except (gws.GwsError, ValueError) as exc:
        ctx.log.warning("brief: Gmail draft not saved (%s)", type(exc).__name__)
        return None, f"Gmail draft failed: {exc}"
    ctx.log.info("brief: Gmail draft saved")
    return made.get("id"), None


def run_brief(ctx: JobContext, draft: bool = True) -> JobResult:
    from . import engine

    tz_name = ctx.config.timezone
    since = ctx.since or (ctx.now - DEFAULT_WINDOW)
    stem = free_stem(ctx.ws.briefs, ctx.now.astimezone(clock.tz(tz_name)).strftime("%Y-%m-%d"))
    target = ctx.ws.briefs / f"{stem}.md"
    started = time.time()
    packet = build_packet(ctx, stem)
    ctx.log.info("brief: packet ready (%d mail, %d slack, %d tasks, %d source errors)",
                 len((packet.get("mail") or {}).get("messages") or []), len(packet.get("slack") or []),
                 len(packet.get("tasks") or []), len(packet["errors"]))
    args_text = (f"Data packet: briefs/.packets/{stem}.json\nWrite the brief to: briefs/{stem}.md\n"
                 f"It covers {day_label(since, tz_name)} to {day_label(ctx.now, tz_name)}.")
    result = engine.run_skill(ctx, BRIEF_SKILL, args_text, engine.DEFAULT_TOOLS)
    data: Dict[str, Any] = {"path": f"briefs/{stem}.md", "fallback": False, "draft_id": None,
                            "cost_usd": result.cost_usd}
    if fresh_output(target, started):
        text = target.read_text(encoding="utf-8", errors="replace")
        if result.reason:
            data["engine_note"] = result.reason
    else:
        reason = result.reason or "the AI didn't write the brief"
        data.update(fallback=True, engine_error=reason)
        text = render_fallback(packet, reason, tz_name)
        ctx.log.warning("brief: AI summary unavailable (%s); wrote the fallback brief", reason)
    text = ensure_stamp(text, stamp_line(ctx.now, since, tz_name))
    write_private(target, text)
    if draft and ctx.config.brief_gmail_draft:
        data["draft_id"], note = _gmail_draft(ctx, text)
        if note:
            data["draft_note"] = note
    missing = missing_sources(packet)
    data.update(window_start=clock.to_iso(since), window_end=packet["window"]["now"],
                complete=not (missing or data["fallback"]))
    if missing:
        data["missing"] = missing
    return JobResult("ok", "fallback brief (AI unavailable)" if data["fallback"] else "brief ready", data=data)


def missing_sources(packet: Dict[str, Any]) -> List[str]:
    """Window sources this brief couldn't read. When any is missing (or the fallback was used)
    the brief records complete=False, and the next brief covers this window again."""
    failed = {item.get("source") for item in packet.get("errors") or []}
    missing = [name for name in WINDOW_SOURCES if name in failed]
    if (packet.get("slack_status") or {}).get("status") == "error" and "slack" not in missing:
        missing.append("slack")
    return missing


# ---------------------------------------------------------------- CLI (brief_cli.py)


def register(subparsers) -> None:
    parser = subparsers.add_parser("brief", help="write your morning brief now")
    parser.add_argument("--no-draft", action="store_true", help="don't save a copy in Gmail drafts")
    parser.add_argument("--detach", action="store_true",
                        help="write it in the background and return at once (for chats)")
    parser.set_defaults(func=_run_cli)


def _run_cli(args: argparse.Namespace) -> int:
    from .brief_cli import run_cli

    return run_cli(args)
