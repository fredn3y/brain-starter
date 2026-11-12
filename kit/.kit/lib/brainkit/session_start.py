"""`brain _session-start`: Claude Code's SessionStart hook (internal, never typed by a person).

Today only the scheduled job checks GitHub for a new kit release, so a brain without the daily
schedule would never hear of one. This hook closes that gap: at most once per
`update_check_hours` it runs the same check (short network timeout) and records an ok answer as
an `update_check` row in the runs ledger, exactly like the scheduled job, so the two never
disagree. When the newest recorded answer says an update is waiting, it prints ONE plain line,
which Claude Code adds to the chat's context; that line goes out at most once per
`update_check_hours` per version, so the user isn't asked in every chat.

It always exits 0 and prints nothing on any problem: a hook must never get in the way of a chat.
Tries (including failed ones) are stamped in `.kit/state/session-start.json`; failures are not
written to the runs ledger, so a laptop that's offline doesn't put errors in the morning brief,
and a failed try is repeated after an hour rather than a day. In scheduled (headless) runs,
which engine.py marks with BRAIN_ENGINE_RUN, it does nothing: the brief reports updates itself,
and the chat's once-a-day notice must not be used up by a run nobody reads.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from . import clock
from . import config as config_mod
from .paths import Workspace
from .runner import Runner
from .runs import RunsLedger

COMMAND = "_session-start"
STATE_FILE = "session-start.json"
NETWORK_TIMEOUT = 4  # seconds; settings.json gives the whole hook 10
RETRY_AFTER_FAILURE = timedelta(hours=1)  # offline or GitHub down: try again sooner
ENGINE_RUN_ENV = "BRAIN_ENGINE_RUN"  # engine.py sets it for scheduled (headless) runs
LINE = ('A brain kit update is available (v{current} -> v{latest}{headline}). Tell the user in '
        'one line and offer to run it; if they say yes, run "brain update --yes".')


def _state_path(ws: Workspace) -> Path:
    return ws.state / STATE_FILE


def _load_state(ws: Workspace) -> Dict[str, Any]:
    try:
        data = json.loads(_state_path(ws).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_state(ws: Workspace, state: Dict[str, Any]) -> None:
    path = _state_path(ws)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _recent(stamp: Any, now: datetime, every: timedelta) -> bool:
    when = clock.parse_iso(stamp) if isinstance(stamp, str) else None
    return when is not None and timedelta(0) <= now - when < every


def headline(notes: str, limit: int = 120) -> str:
    """The first line of the release notes, without Markdown marks or control characters."""
    for line in (notes or "").splitlines():
        text = re.sub(r"[\x00-\x1f\x7f]", "", line)
        text = re.sub(r"\s+", " ", text.strip().lstrip("#*->_ ").strip(" *_`"))
        if text:
            return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"
    return ""


def update_line(ws: Workspace, runner: Runner, now: Optional[datetime] = None,
                check: Optional[Callable[..., Any]] = None) -> str:
    """The line to print for Claude ("" for nothing). May raise; `run` swallows everything."""
    from .jobs.update_check import check as default_check
    from .schedule import sanitize
    from .update import installed_version, parse_version

    cfg, _ = sanitize(config_mod.load(ws))
    now = now or clock.now(cfg.timezone)
    every = timedelta(hours=cfg.update_check_hours)
    runs = RunsLedger(ws)
    state = _load_state(ws)
    last = runs.last("update_check")
    if not (last and _recent(last.get("finished_at"), now, every)) and \
            not _recent(state.get("checked_at"), now, every):
        state["checked_at"] = clock.to_iso(now)  # stamped first: a killed hook still counts
        _save_state(ws, state)
        started = time.monotonic()
        result = (check or default_check)(ws, runner, cfg.kit_repo, timeout=NETWORK_TIMEOUT)
        if result.status != "ok" and every > RETRY_AFTER_FAILURE:
            state["checked_at"] = clock.to_iso(now - every + RETRY_AFTER_FAILURE)
            _save_state(ws, state)
        if result.status == "ok":
            data = dict(result.data or {}, source="session-start")
            finished = now + timedelta(seconds=time.monotonic() - started)
            runs.record("update_check", "ok", now, finished, summary=result.summary or "",
                        data=data)
            last = runs.last("update_check")
    data = (last or {}).get("data") or {}
    latest, current = data.get("latest"), installed_version(ws)
    if not (data.get("update_available") and isinstance(latest, str)):
        return ""
    if parse_version(latest) <= parse_version(current):
        return ""  # already updated since that answer
    told = state.get("told") if isinstance(state.get("told"), dict) else {}
    if told.get("version") == latest and _recent(told.get("at"), now, every):
        return ""
    state["told"] = {"version": latest, "at": clock.to_iso(now)}
    _save_state(ws, state)
    title = headline(str(data.get("notes") or ""))
    return LINE.format(current=current, latest=latest, headline=f": {title}" if title else "")


def _workspace(args) -> Workspace:
    """--workspace, else the brain this code is installed in, else $BRAIN_HOME or ~/brain."""
    value = getattr(args, "workspace", None)
    if value:
        return Workspace(Path(value))
    here = Path(__file__).resolve().parents[3]  # <brain>/.kit/lib/brainkit/session_start.py
    if (here / ".kit" / "manifest.json").is_file():
        return Workspace(here)
    return Workspace.default()


def run(args) -> int:
    try:
        if os.environ.get(ENGINE_RUN_ENV):  # a scheduled run: the brief already reports updates
            return 0
        ws = _workspace(args)
        line = update_line(ws, Runner())
        if line:
            sys.stdout.write(line + "\n")
            sys.stdout.flush()
    except BaseException:  # noqa: B036 - a hook must never fail, block or talk about errors
        pass
    return 0


def register(subparsers) -> None:
    # No help= so it stays out of `brain --help`: only Claude Code's hook runs it.
    parser = subparsers.add_parser(COMMAND, description="(internal) Claude Code session-start hook")
    parser.set_defaults(func=run)
