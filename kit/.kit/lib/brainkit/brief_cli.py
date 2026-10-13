"""`brain brief [--no-draft] [--detach]`: write the morning brief now.

It runs like the schedule and under the same run lock: a Slack sync, then the brief, both
recorded in the runs ledger. If a catch-up already holds the lock it exits quietly, so two
briefs are never written at once. `--detach` starts the same command as its own background
process and returns at once, so a chat's short command timeout can't kill a brief half-way.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path

from . import clock
from .paths import Workspace

DETACHED_LOG = "brief-detached.log"
DETACHED_MESSAGE = "Writing your brief in the background (1–3 minutes). Ask me to show it when it's ready."


def _workspace(args: argparse.Namespace) -> Workspace:
    value = getattr(args, "workspace", None)
    return Workspace(Path(value)) if value else Workspace.default()


def detach(ws: Workspace, no_draft: bool) -> int:
    """Start `brain brief` as its own background process (new session, so a chat's command
    timeout can't kill it) and return at once. Output goes to logs/brief-detached.log."""
    import subprocess
    import sys

    entry = Path(__file__).resolve().parents[2] / "bin" / "brain"
    argv = [sys.executable, str(entry), "--workspace", str(ws.root), "brief"] + (["--no-draft"] if no_draft else [])
    ws.logs.mkdir(parents=True, exist_ok=True)
    try:
        with open(str(ws.logs / DETACHED_LOG), "a", encoding="utf-8") as log_file:
            subprocess.Popen(argv, cwd=str(ws.root), stdin=subprocess.DEVNULL, stdout=log_file,
                             stderr=subprocess.STDOUT, start_new_session=True, close_fds=True)
    except OSError as exc:
        print(f"Couldn't start the brief in the background ({exc}). Try `brain brief` instead.")
        return 1
    print(DETACHED_MESSAGE)
    return 0


def run_cli(args: argparse.Namespace) -> int:
    """A Slack sync, then the brief, under the run lock; exits quietly (0) when it's taken."""
    from . import catchup
    from . import config as config_mod
    from .lock import try_lock
    from .log import get_logger
    from .notify import make_notifier
    from .runner import Runner
    from .runs import RunsLedger
    from .schedule import DueJob, sanitize, window_since

    ws = _workspace(args)
    cfg, _ = sanitize(config_mod.load(ws))
    if args.no_draft:
        cfg = replace(cfg, brief_gmail_draft=False)
    ws.ensure_dirs()
    if getattr(args, "detach", False):
        return detach(ws, args.no_draft)
    runner = Runner()
    log = get_logger(ws)
    with try_lock(ws.lock_file) as locked:
        if not locked:
            log.info("brief: not started, another run holds the lock")
            print("Another brain run is busy right now. Try again in a minute.")
            return 0
        runs = RunsLedger(ws)
        now = clock.now(cfg.timezone)
        due = [DueJob("slack_sync", "requested"), DueJob("brief", "requested", window_since(cfg, runs, "brief", now))]
        results = catchup._run_due(ws, cfg, runner, now, make_notifier(runner, log), log, runs, due, None)
    for name, result in results.items():
        print(f"{name}: {result.status}" + (f" — {result.summary}" if result.summary else ""))
    path = (results["brief"].data or {}).get("path")
    if results["brief"].status == "ok" and path:
        print(f"Your brief: {ws.root / path}")
    return 1 if results["brief"].status == "error" else 0
