"""Slack → `ledgers/slack/YYYY-MM.jsonl`: cursor-based sync of the user's conversations, and readers.

A sync lists every conversation the user is in (channels, private channels, DMs, group DMs),
reads messages newer than that conversation's cursor and appends the human ones to the monthly
ledger, deduplicated by id ("<channel>:<ts>"). Thread replies: history is read back
`thread_lookback_hours` (default 72) so a thread with a new reply is noticed even when its
parent is older than the cursor; a thread is fetched when its latest reply isn't in the ledger
yet, at most `max_threads` threads per sync. The cursor (`ledgers/slack-cursor.json`) is written
atomically after each ledger append: a crash can repeat work but never skips messages.

Logs carry counts and conversation ids only, never message text. The `brain slack …` commands
live in slack_cli.py.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

from . import clock, slack_api
from .paths import Workspace
from .runner import Runner
from .slack_api import SlackError

# Message subtypes that are not human messages (system, edit and bot events). Everything else,
# including no subtype, file_share and thread_broadcast, is kept.
SKIP_SUBTYPES = frozenset({
    "channel_join", "channel_leave", "channel_topic", "channel_purpose", "channel_name",
    "channel_archive", "channel_unarchive", "group_join", "group_leave", "group_topic",
    "group_purpose", "group_name", "message_changed", "message_deleted", "message_replied",
    "bot_message", "bot_add", "bot_remove", "reminder_add", "tombstone",
})
CONVERSATION_TYPES = "public_channel,private_channel,mpim,im"
DM_KINDS = ("dm", "group_dm")
PAGE_LIMIT = 200
HISTORY_PAGES = 20
REPLY_PAGES = 5
CONVERSATION_PAGES = 20
TEXT_LIMIT = 2000
THREAD_LOOKBACK_HOURS = 72
DEDUPE_FILES = 2
LINE_TEXT = 140


def _ts(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def is_human(msg: Dict[str, Any]) -> bool:
    return not msg.get("bot_id") and msg.get("subtype") not in SKIP_SUBTYPES


def conversation_kind(conv: Dict[str, Any]) -> str:
    if conv.get("is_im"):
        return "dm"
    if conv.get("is_mpim"):
        return "group_dm"
    if conv.get("is_private") or conv.get("is_group"):
        return "private"
    return "channel"


def conversation_name(conv: Dict[str, Any], kind: str, names: Dict[str, str]) -> str:
    if kind == "dm":
        user = str(conv.get("user") or "")
        return f"DM with {names.get(user, user) or 'someone'}"
    if kind == "group_dm":
        match = re.match(r"^mpdm-(.+)-\d+$", str(conv.get("name") or ""))
        members = [part for part in match.group(1).split("--") if part] if match else []
        return "Group DM" + (f" ({', '.join(members)})" if members else "")
    return "#" + str(conv.get("name") or conv.get("id") or "")


def readable(text: str, names: Optional[Dict[str, str]] = None) -> str:
    """Slack markup to plain text: <@U1> → @Name, <#C1|general> → #general, <url|label> → label."""
    names = names or {}

    def swap(match: "re.Match[str]") -> str:
        body = match.group(1)
        target, _, label = body.partition("|")
        if target.startswith("@"):
            return "@" + (names.get(target[1:]) or label or target[1:])
        if target.startswith("#"):
            return "#" + (label or target[1:])
        if target.startswith("!"):
            word = label or target[1:].split("^")[0]
            return word if word.startswith("@") else "@" + word
        return label or target

    out = re.sub(r"<([^<>]+)>", swap, text or "")
    return out.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")


# ---------------------------------------------------------------- files


def users_path(ws: Workspace) -> Path:
    return ws.ledgers / "slack-users.json"


def load_json(path: Path, default: Dict[str, Any]) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return dict(default)
    return data if isinstance(data, dict) else dict(default)


def save_json(path: Path, data: Dict[str, Any]) -> None:
    """Atomic write (tmp file + os.replace), private to the user."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with os.fdopen(os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w",
                   encoding="utf-8") as handle:
        json.dump(data, handle, indent=1, sort_keys=True, ensure_ascii=False)
    os.replace(str(tmp), str(path))


def month_files(ws: Workspace) -> List[Path]:
    try:
        return sorted(p for p in ws.slack_ledger_dir.iterdir() if re.match(r"^\d{4}-\d{2}\.jsonl$", p.name))
    except OSError:
        return []


def read_rows(path: Path) -> Iterator[Dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue  # a torn line must not stop the reader
        if isinstance(row, dict):
            yield row


def _month(ts: float, tz) -> str:
    return datetime.fromtimestamp(ts, tz).strftime("%Y-%m")


def known_ids(ws: Workspace, months: Sequence[str]) -> set:
    paths = set(month_files(ws)[-DEDUPE_FILES:]) | {ws.slack_ledger_dir / f"{m}.jsonl" for m in months}
    return {row.get("id") for path in paths for row in read_rows(path)}


def append_entries(ws: Workspace, entries: List[Dict[str, Any]], tz) -> None:
    by_month: Dict[str, List[Dict[str, Any]]] = {}
    for entry in entries:
        by_month.setdefault(_month(float(entry["ts"]), tz), []).append(entry)
    ws.slack_ledger_dir.mkdir(parents=True, exist_ok=True)
    for month, rows in sorted(by_month.items()):
        path = ws.slack_ledger_dir / f"{month}.jsonl"
        prefix = ""
        if path.exists() and path.stat().st_size:
            with open(str(path), "rb") as handle:
                handle.seek(-1, os.SEEK_END)
                prefix = "" if handle.read(1) == b"\n" else "\n"  # never glue onto a torn line
        with os.fdopen(os.open(str(path), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600), "a",
                       encoding="utf-8") as handle:
            handle.write(prefix + "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))


# ---------------------------------------------------------------- sync


class _Sync:
    def __init__(self, ws: Workspace, client: slack_api.Client, now: datetime, log,
                 initial_hours: float, thread_hours: float, backfill_hours: Optional[float], max_threads: int):
        self.ws, self.client, self.log = ws, client, log
        self.tz = now.tzinfo or timezone.utc
        self.now_ts = now.timestamp()
        self.initial_hours, self.thread_hours, self.backfill_hours = initial_hours, thread_hours, backfill_hours
        self.thread_budget = max_threads
        self.names: Dict[str, str] = {k: v for k, v in load_json(users_path(ws), {}).items() if isinstance(v, str)}
        self.names_before = dict(self.names)
        self.failed: set = set()
        self.me, self.base_url, self.mention = "", "https://slack.com/", None
        self.known: set = set()
        self.summary = {"conversations": 0, "messages": 0, "dms": 0, "mentions": 0, "errors": 0,
                        "threads": 0, "threads_deferred": 0, "gaps": 0}

    def start(self) -> List[Dict[str, Any]]:
        me = self.client.call("auth.test")
        self.me = str(me.get("user_id") or "")
        self.base_url = str(me.get("url") or self.base_url).rstrip("/") + "/"
        self.mention = re.compile(r"<@" + re.escape(self.me) + r"(\|[^>]*)?>") if self.me else None
        reach = max(self.initial_hours, self.thread_hours, self.backfill_hours or 0) * 3600
        first = self.now_ts - reach
        months = sorted({_month(first, self.tz), _month(self.now_ts, self.tz)})
        self.known = known_ids(self.ws, months)
        return self.client.paginate("users.conversations", {"types": CONVERSATION_TYPES, "exclude_archived": True,
                                                            "limit": PAGE_LIMIT}, "channels", CONVERSATION_PAGES)

    def window(self, cursor: Optional[str]) -> Tuple[float, float]:
        """(start, scan_from): messages after `start` are new; history is read from `scan_from`."""
        start = _ts(cursor) if cursor else None
        if start is None:
            start = self.now_ts - self.initial_hours * 3600
        if self.backfill_hours:
            start = min(start, self.now_ts - self.backfill_hours * 3600)
        return start, min(start, self.now_ts - self.thread_hours * 3600)

    def conversation(self, conv: Dict[str, Any], cursor: Optional[str]) -> Tuple[List[Dict[str, Any]], str]:
        cid = str(conv["id"])
        kind = conversation_kind(conv)
        start, scan_from = self.window(cursor)
        history = self.client.paginate("conversations.history", {
            "channel": cid, "oldest": f"{scan_from:.6f}", "limit": PAGE_LIMIT, "inclusive": False},
            "messages", HISTORY_PAGES)
        newest = cursor if cursor and _ts(cursor) is not None else f"{start:.6f}"
        fresh: List[Dict[str, Any]] = []
        parents: List[Dict[str, Any]] = []
        oldest_seen = None
        for msg in history:
            ts = _ts(msg.get("ts"))
            if ts is None or ts <= scan_from:
                continue
            oldest_seen = ts if oldest_seen is None else min(oldest_seen, ts)
            if ts > (_ts(newest) or 0.0):
                newest = str(msg["ts"])
            if ts > start and is_human(msg):
                fresh.append(msg)
            latest = _ts(msg.get("latest_reply"))
            if msg.get("thread_ts") == msg.get("ts") and latest is not None and latest > scan_from:
                parents.append(msg)
        if not history.complete and (oldest_seen is None or oldest_seen > start):
            self.summary["gaps"] += 1
            self.log.warning("slack sync: conversation %s had more new messages than one sync reads; "
                             "the oldest were skipped", cid)
        replies = self.thread_replies(cid, parents, scan_from)
        users = [str(m.get("user") or "") for m in fresh + replies] + [str(conv.get("user") or "")]
        slack_api.lookup_names(self.client, sorted(set(u for u in users if u)), self.names, self.failed)
        name = conversation_name(conv, kind, self.names)
        entries = []
        for msg in fresh + replies:
            entry = self.entry(msg, cid, name, kind)
            if entry["id"] in self.known:
                continue
            self.known.add(entry["id"])
            entries.append(entry)
        entries.sort(key=lambda e: float(e["ts"]))
        return entries, newest

    def thread_replies(self, cid: str, parents: List[Dict[str, Any]], scan_from: float) -> List[Dict[str, Any]]:
        found: List[Dict[str, Any]] = []
        for parent in parents:
            if f"{cid}:{parent.get('latest_reply')}" in self.known:
                continue  # the newest reply is already in the ledger
            if self.thread_budget <= 0:
                self.summary["threads_deferred"] += 1
                continue
            self.thread_budget -= 1
            self.summary["threads"] += 1
            replies = self.client.paginate("conversations.replies", {
                "channel": cid, "ts": parent["ts"], "oldest": f"{scan_from:.6f}", "limit": PAGE_LIMIT,
                "inclusive": False}, "messages", REPLY_PAGES)
            for reply in replies:
                ts = _ts(reply.get("ts"))
                if reply.get("ts") != parent.get("ts") and ts is not None and ts > scan_from and is_human(reply):
                    found.append(reply)
        return found

    def entry(self, msg: Dict[str, Any], cid: str, name: str, kind: str) -> Dict[str, Any]:
        ts = str(msg.get("ts"))
        user = str(msg.get("user") or "")
        text = str(msg.get("text") or "").strip()
        thread_ts = msg.get("thread_ts")
        in_thread = bool(thread_ts) and thread_ts != ts
        link = f"{self.base_url}archives/{cid}/p{ts.replace('.', '')}"
        if in_thread:
            link += f"?thread_ts={thread_ts}&cid={cid}"
        files = [{"name": str(f.get("name") or f.get("title") or "file"), "mimetype": str(f.get("mimetype") or "")}
                 for f in msg.get("files") or [] if isinstance(f, dict)]
        return {
            "id": f"{cid}:{ts}", "channel": cid, "channel_name": name, "kind": kind, "ts": ts,
            "time": datetime.fromtimestamp(float(ts), self.tz).isoformat(timespec="seconds"),
            "user": user, "user_name": self.names.get(user, user),
            "from_me": bool(self.me) and user == self.me,
            "mentions_me": bool(self.mention and self.mention.search(text)),
            "text": text[:TEXT_LIMIT], "permalink": link, "thread_ts": thread_ts,
            "reply_count": int(msg.get("reply_count") or 0), "in_thread": in_thread, "files": files,
        }

    def count(self, entries: List[Dict[str, Any]]) -> None:
        for entry in entries:
            if entry["from_me"]:
                continue
            self.summary["messages"] += 1
            self.summary["dms"] += entry["kind"] in DM_KINDS
            self.summary["mentions"] += bool(entry["mentions_me"])


def sync(ws: Workspace, runner: Runner, token: str, now: datetime, log, initial_lookback_hours: float = 24,
         sleep=time.sleep, pause: float = 1.2, max_threads: int = 20, backfill_hours: Optional[float] = None,
         thread_lookback_hours: float = THREAD_LOOKBACK_HOURS) -> Dict[str, int]:
    """One incremental sync. Returns counts: conversations, messages (new, not from the user),
    dms, mentions, errors, threads, threads_deferred, gaps. Raises SlackError for token problems."""
    client = slack_api.Client(runner, token, sleep=sleep, pause=pause)
    state = _Sync(ws, client, now, log, initial_lookback_hours, thread_lookback_hours, backfill_hours, max_threads)
    conversations = state.start()
    cursor = {k: v for k, v in load_json(ws.slack_cursor, {}).items() if isinstance(v, str)}
    for conv in conversations:
        cid = conv.get("id") if isinstance(conv, dict) else None
        if not cid or conv.get("is_archived") or conv.get("is_user_deleted"):
            continue
        state.summary["conversations"] += 1
        try:
            entries, newest = state.conversation(conv, cursor.get(cid))
        except Exception as exc:  # one bad conversation must not stop the rest
            if isinstance(exc, SlackError) and exc.token_problem:
                raise
            code = exc.error if isinstance(exc, SlackError) else type(exc).__name__  # never the message text
            state.summary["errors"] += 1
            log.warning("slack sync: skipped conversation %s (%s)", cid, code or "request failed")
            if code == "ratelimited":
                break  # stop for now; the cursors keep every conversation's place
            continue
        if entries:
            append_entries(ws, entries, state.tz)
            state.count(entries)
        if newest != cursor.get(cid):
            cursor[cid] = newest
            save_json(ws.slack_cursor, cursor)  # after the ledger append, never before
    if state.names != state.names_before:
        save_json(users_path(ws), state.names)
    log.info("slack sync: %d conversations, %d new messages, %d threads, %d errors",
             state.summary["conversations"], state.summary["messages"], state.summary["threads"],
             state.summary["errors"])
    return state.summary


def summary_text(summary: Dict[str, int]) -> str:
    def plural(count: int, word: str) -> str:
        return f"{count} {word}" + ("" if count == 1 else "s")

    text = (f"{plural(summary.get('messages', 0), 'new message')} ({plural(summary.get('dms', 0), 'DM')}, "
            f"{plural(summary.get('mentions', 0), 'mention')})")
    if summary.get("errors"):
        text += f"; {plural(summary['errors'], 'conversation')} failed"
    return text


def recent(ws: Workspace, since: datetime, kinds: Optional[Sequence[str]] = None, mentions_only: bool = False,
           limit: int = 200) -> List[Dict[str, Any]]:
    """Ledger rows newer than `since`, oldest first (the newest `limit` of them)."""
    since_ts = since.timestamp()
    first_month = (since - timedelta(days=1)).strftime("%Y-%m")
    rows: Dict[str, Dict[str, Any]] = {}
    for path in month_files(ws):
        if path.stem < first_month:
            continue
        for row in read_rows(path):
            ts = _ts(row.get("ts"))
            if ts is None or ts < since_ts:
                continue
            if (kinds and row.get("kind") not in kinds) or (mentions_only and not row.get("mentions_me")):
                continue
            rows[str(row.get("id"))] = row
    ordered = sorted(rows.values(), key=lambda r: _ts(r.get("ts")) or 0.0)
    return ordered[-limit:] if limit and limit > 0 else ordered


def format_line(row: Dict[str, Any], tz_name: str, names: Optional[Dict[str, str]] = None) -> str:
    """'Tue 09:12 · #general · Name: text… <permalink>'."""
    when = clock.fmt_local(datetime.fromtimestamp(_ts(row.get("ts")) or 0.0, timezone.utc), tz_name)
    text = re.sub(r"\s+", " ", readable(str(row.get("text") or ""), names)).strip()
    if not text and row.get("files"):
        text = "[file: " + ", ".join(str(f.get("name")) for f in row["files"] if isinstance(f, dict)) + "]"
    if len(text) > LINE_TEXT:
        text = text[: LINE_TEXT - 1].rstrip() + "…"
    who = row.get("user_name") or row.get("user") or "someone"
    return f"{when} · {row.get('channel_name') or row.get('channel')} · {who}: {text} <{row.get('permalink')}>"
