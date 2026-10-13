"""`brain slack …`: read the user's Slack from the terminal or chat (the brain never posts).

  brain slack whoami                      which account the saved token belongs to
  brain slack sync [--lookback-days N]    fetch new messages now (also re-read N days back)
  brain slack recent [--hours H] [--dms] [--mentions] [--json]   from the brain's copy
  brain slack search QUERY [--count N]    live search (needs the search:read scope)
  brain slack thread CHANNEL TS           a whole thread, live
  brain slack channels                    conversations the user is in
  brain slack create-app                  open Slack's "create app" page pre-filled from the kit's manifest

Output lines look like "Tue 09:12 · #general · Name: text… <permalink>". The token is never printed.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import timedelta
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from . import clock, slack_api
from .browser import open_url
from .paths import Workspace
from .runner import Runner
from .slack_api import SlackError
from .slack_sync import (CONVERSATION_PAGES, CONVERSATION_TYPES, DM_KINDS, PAGE_LIMIT, REPLY_PAGES,
                         conversation_kind, conversation_name, format_line, load_json, recent, save_json,
                         summary_text, sync, users_path)

MANIFEST_URL = "https://api.slack.com/apps?new_app=1&manifest_yaml="
NOT_CONNECTED = ("Slack isn't connected yet. Run `brain slack create-app`, install the app, then save "
                 "its User OAuth Token with `brain secret set slack --gui`.")


def manifest_url(manifest_yaml: str) -> str:
    return MANIFEST_URL + quote(manifest_yaml, safe="")


def _workspace(args: argparse.Namespace) -> Workspace:
    value = getattr(args, "workspace", None)
    return Workspace(Path(value)) if value else Workspace.default()


def _runner() -> Runner:
    return Runner()


def _platform() -> str:
    return sys.platform


def _tz_name(ws: Workspace) -> str:
    from . import config as config_mod
    from .schedule import sanitize

    return sanitize(config_mod.load(ws))[0].timezone


def _token(runner: Runner) -> Optional[str]:
    from . import secrets

    try:
        token = secrets.get(runner, "slack")
    except secrets.SecretError as exc:
        print(f"Couldn't read the Slack token: {exc}", file=sys.stderr)
        return None
    if not token:
        print(NOT_CONNECTED, file=sys.stderr)
    return token


def register(subparsers) -> None:
    parser = subparsers.add_parser("slack", help="read your Slack (the brain never posts)")
    actions = parser.add_subparsers(dest="slack_action", metavar="<action>")
    actions.add_parser("whoami", help="which Slack account is connected").set_defaults(func=cmd_whoami)
    p_sync = actions.add_parser("sync", help="fetch new messages now")
    p_sync.add_argument("--lookback-days", type=float, help="also re-read this many days back (max 31)")
    p_sync.set_defaults(func=cmd_sync)
    p_recent = actions.add_parser("recent", help="recent messages from the brain's copy")
    p_recent.add_argument("--hours", type=float, default=24.0, help="how far back (default 24)")
    p_recent.add_argument("--dms", action="store_true", help="direct messages only")
    p_recent.add_argument("--mentions", action="store_true", help="messages that mention you only")
    p_recent.add_argument("--json", action="store_true", help="print JSON")
    p_recent.set_defaults(func=cmd_recent)
    p_search = actions.add_parser("search", help="search Slack")
    p_search.add_argument("query")
    p_search.add_argument("--count", type=int, default=20, help="how many results (max 100)")
    p_search.set_defaults(func=cmd_search)
    p_thread = actions.add_parser("thread", help="show a whole thread")
    p_thread.add_argument("channel", help="conversation id, e.g. C0123456")
    p_thread.add_argument("ts", help="the thread's first message ts, e.g. 1760000000.000100")
    p_thread.set_defaults(func=cmd_thread)
    actions.add_parser("channels", help="list your conversations").set_defaults(func=cmd_channels)
    actions.add_parser("create-app", help="open Slack's page to create the brain's Slack app"
                       ).set_defaults(func=cmd_create_app)
    parser.set_defaults(func=lambda args: _usage(parser))


def _usage(parser: argparse.ArgumentParser) -> int:
    parser.print_help()
    return 2


def cmd_whoami(args: argparse.Namespace) -> int:
    runner = _runner()
    token = _token(runner)
    if not token:
        return 1
    try:
        me = slack_api.auth_test(runner, token)
    except SlackError as exc:
        print(f"Slack: {exc}", file=sys.stderr)
        return 1
    if not me.get("ok"):
        print(f"Slack rejected the saved token ({me.get('error')}). To fix, {slack_api.TOKEN_FIX}.", file=sys.stderr)
        return 1
    print(f"Connected to Slack as {me.get('user')} ({me.get('user_id')}) in {me.get('team')} — {me.get('url')}")
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    from .lock import try_lock
    from .log import get_logger

    ws = _workspace(args)
    runner = _runner()
    token = _token(runner)
    if not token:
        return 1
    backfill = min(float(args.lookback_days), 31.0) * 24 if args.lookback_days else None
    with try_lock(ws.lock_file) as locked:
        if not locked:
            print("A scheduled run is busy right now. Try again in a minute.")
            return 1
        try:
            summary = sync(ws, runner, token, clock.now(_tz_name(ws)), get_logger(ws), backfill_hours=backfill)
        except SlackError as exc:
            print(f"Slack sync failed: {exc}", file=sys.stderr)
            return 1
    print(summary_text(summary))
    return 0


def cmd_recent(args: argparse.Namespace) -> int:
    ws = _workspace(args)
    tz_name = _tz_name(ws)
    since = clock.now(tz_name) - timedelta(hours=max(0.0, args.hours))
    rows = recent(ws, since, kinds=DM_KINDS if args.dms else None, mentions_only=args.mentions)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return 0
    if not rows:
        print(f"No messages in the last {args.hours:g} hours. (`brain slack sync` fetches new ones.)")
    names = load_json(users_path(ws), {})
    for row in rows:
        print(format_line(row, tz_name, names))
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    ws = _workspace(args)
    runner = _runner()
    token = _token(runner)
    if not token:
        return 1
    try:
        found = slack_api.call(runner, "search.messages", token, {
            "query": args.query, "count": min(max(args.count, 1), 100), "sort": "timestamp", "sort_dir": "desc"})
    except SlackError as exc:
        print(f"Slack search failed: {exc}", file=sys.stderr)
        return 1
    tz_name = _tz_name(ws)
    names = load_json(users_path(ws), {})
    matches = (found.get("messages") or {}).get("matches") or []
    for match in matches:
        channel = match.get("channel") or {}
        label = "DM" if channel.get("is_im") else "Group DM" if channel.get("is_mpim") else f"#{channel.get('name')}"
        print(format_line({"ts": match.get("ts"), "channel_name": label, "text": match.get("text"),
                           "user_name": match.get("username") or match.get("user"),
                           "permalink": match.get("permalink")}, tz_name, names))
    if not matches:
        print("No matches.")
    return 0


def cmd_thread(args: argparse.Namespace) -> int:
    ws = _workspace(args)
    runner = _runner()
    token = _token(runner)
    if not token:
        return 1
    client = slack_api.Client(runner, token)
    try:
        me = client.call("auth.test")
        messages = client.paginate("conversations.replies", {"channel": args.channel, "ts": args.ts,
                                                             "limit": PAGE_LIMIT}, "messages", REPLY_PAGES)
        names = load_json(users_path(ws), {})
        slack_api.lookup_names(client, sorted({str(m.get("user") or "") for m in messages} - {""}), names)
    except SlackError as exc:
        print(f"Couldn't read the thread: {exc}", file=sys.stderr)
        return 1
    base = str(me.get("url") or "https://slack.com/").rstrip("/") + "/"
    tz_name = _tz_name(ws)
    for msg in messages:
        ts = str(msg.get("ts"))
        link = f"{base}archives/{args.channel}/p{ts.replace('.', '')}"
        if ts != args.ts:
            link += f"?thread_ts={args.ts}&cid={args.channel}"
        print(format_line({"ts": ts, "channel_name": args.channel, "text": msg.get("text"), "files": msg.get("files"),
                           "user_name": names.get(str(msg.get("user") or ""), msg.get("user")),
                           "permalink": link}, tz_name, names))
    return 0


def cmd_channels(args: argparse.Namespace) -> int:
    ws = _workspace(args)
    runner = _runner()
    token = _token(runner)
    if not token:
        return 1
    client = slack_api.Client(runner, token)
    names = load_json(users_path(ws), {})
    try:
        convs = client.paginate("users.conversations", {"types": CONVERSATION_TYPES, "exclude_archived": True,
                                                        "limit": PAGE_LIMIT}, "channels", CONVERSATION_PAGES)
        slack_api.lookup_names(client, sorted({str(c.get("user") or "") for c in convs} - {""}), names)
    except SlackError as exc:
        print(f"Couldn't list your conversations: {exc}", file=sys.stderr)
        return 1
    save_json(users_path(ws), names)
    order = {"channel": 0, "private": 1, "group_dm": 2, "dm": 3}
    rows = sorted(((conversation_kind(c), conversation_name(c, conversation_kind(c), names), str(c.get("id")))
                   for c in convs if c.get("id")), key=lambda r: (order[r[0]], r[1].lower()))
    for kind, name, cid in rows:
        print(f"{kind:8}  {name}  ({cid})")
    return 0


def cmd_create_app(args: argparse.Namespace) -> int:
    ws = _workspace(args)
    path = ws.kit / "slack-manifest.yaml"
    try:
        manifest = path.read_text(encoding="utf-8")
    except OSError:
        print(f"The Slack app manifest is missing ({path}). Run `brain update` (or reinstall the kit), "
              "then try again.", file=sys.stderr)
        return 1
    url = manifest_url(manifest)
    print("Opening Slack's “Create an app” page with the brain's settings filled in.")
    print("Pick your workspace, click Create, then Install to Workspace, and copy the User OAuth Token.")
    print("If no browser window opens, copy this link into your browser:")
    print(url)
    open_url(_runner(), url, platform=_platform())
    return 0
