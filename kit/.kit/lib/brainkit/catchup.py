"""`brain catchup`: run every due job once, in order, record each run, and notify.

launchd starts this at the brief time and hourly. After a sleep, launchd fires it once on
wake; this runner then catches up every missed job once, so a long weekend asleep produces ONE
brief covering the whole gap. A failing job is recorded and the others still run.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import clock, schedule
from . import config as config_mod
from . import jobs as jobs_pkg
from .config import Config
from .jobs.base import JobContext, JobResult
from .launchagent import job_env
from .lock import try_lock
from .log import get_logger
from .notify import make_notifier
from .paths import Workspace
from .report import redact_secrets
from .runner import Runner
from .runs import RunsLedger

TITLE = "Brain"
STATUSES = ("ok", "error", "skipped")
DETAIL_LIMIT = 500
LAUNCHD_LOG_MAX = 1_000_000
LAUNCHD_LOG_KEEP = 3
Notify = Callable[[str, str], None]


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" + ("" if count == 1 else "s")


def short_detail(text: str) -> str:
    """One line, no known token shapes, at most DETAIL_LIMIT characters."""
    flat = re.sub(r"\s+", " ", redact_secrets(str(text))).strip()
    return flat if len(flat) <= DETAIL_LIMIT else flat[: DETAIL_LIMIT - 1] + "…"


def describe_since(since: datetime, tz_name: str, now: datetime) -> str:
    """'Fri 07:31', or 'Mon 5 Oct 07:30' when it's a week or more back."""
    if schedule.to_utc(now) - schedule.to_utc(since) < timedelta(days=6):
        return clock.fmt_local(since, tz_name)
    local = since.astimezone(clock.tz(tz_name))
    return f"{local.strftime('%a')} {local.day} {local.strftime('%b %H:%M')}"


def plan(config: Config, runs: RunsLedger, now: datetime, only: Optional[str] = None) -> List[schedule.DueJob]:
    if only:
        return [schedule.DueJob(only, "requested", schedule.window_since(config, runs, only, now))]
    return schedule.due_jobs(config, runs, now)


def print_plan(due: List[schedule.DueJob], config: Config, now: datetime) -> None:
    when = clock.fmt_local(now, config.timezone)
    if not due:
        print(f"Nothing is due right now ({when}).")
        return
    print(f"Due now ({when}):")
    width = max(len(item.name) for item in due)
    for item in due:
        line = f"  {item.name.ljust(width)}  {item.reason}"
        if item.since is not None:
            line += f"; covers since {describe_since(item.since, config.timezone, now)}"
        print(line)


def run_job(name: str, ctx: JobContext, log) -> JobResult:
    """Run one job; never raises. Errors become JobResult("error") with a short detail."""
    try:
        func = jobs_pkg.job_runner(name)
    except ImportError as exc:
        log.error("job %s not available: %s", name, exc)
        return JobResult("error", summary="job not available", detail=short_detail(f"job not available: {exc}"))
    except Exception as exc:  # e.g. a syntax error in the job's module
        detail = short_detail(f"job couldn't load: {type(exc).__name__}: {exc}")
        log.error("job %s: %s", name, detail)
        return JobResult("error", summary="job couldn't load", detail=detail)
    try:
        result = func(ctx)
    except (Exception, SystemExit) as exc:  # one job must never stop the others
        detail = short_detail(f"{type(exc).__name__}: {exc}")
        trace = redact_secrets("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
        log.error("job %s failed: %s\n%s", name, detail, trace.rstrip())
        return JobResult("error", summary=f"{name} failed", detail=detail)
    if not isinstance(result, JobResult) or result.status not in STATUSES:
        return JobResult("error", summary=f"{name} failed", detail="the job returned an unexpected result")
    return result


def _plain_data(data: Any) -> Dict[str, Any]:
    """JSON-safe copy of a job's data (datetimes and the like become strings)."""
    try:
        clean = json.loads(json.dumps(data or {}, default=str))
    except (TypeError, ValueError):
        return {}
    return clean if isinstance(clean, dict) else {"value": clean}


def _record(runs: RunsLedger, log, job: str, result: JobResult, started: datetime, finished: datetime) -> None:
    try:
        runs.record(job, result.status, started, finished, summary=result.summary or "",
                    detail=result.detail, data=_plain_data(result.data))
    except OSError as exc:  # a full disk must not stop the remaining jobs
        log.error("couldn't record %s in the runs ledger: %s", job, exc)


def summarize(results: Dict[str, JobResult]) -> str:
    text = "ran: " + ", ".join(results)
    errors = sum(1 for r in results.values() if r.status == "error")
    skipped = sum(1 for r in results.values() if r.status == "skipped")
    if errors:
        text += f"; {_plural(errors, 'error')}"
    if skipped:
        text += f"; {skipped} skipped"
    return text


def start_message(due: List[schedule.DueJob], config: Config, now: datetime) -> Optional[str]:
    by_name = {item.name: item for item in due}
    item = by_name.get("brief") or by_name.get("weekly_review")
    if item is None or item.since is None:
        return None
    return f"Catching up since {describe_since(item.since, config.timezone, now)}…"


def end_message(results: Dict[str, JobResult]) -> Optional[str]:
    """What to tell the user when a run ends. Hourly runs with no brief stay quiet: their
    problems show up at the top of the next brief instead."""
    errors = sum(1 for r in results.values() if r.status == "error")
    brief = results.get("brief")
    weekly = results.get("weekly_review")
    brief_ok = brief is not None and brief.status == "ok"
    weekly_ok = weekly is not None and weekly.status == "ok"
    if errors:
        problems = _plural(errors, "problem")
        if brief_ok or weekly_ok:
            return f"Catch-up finished with {problems} — they're at the top of your brief"
        if brief is None and weekly is None:
            return None
        return f"Catch-up finished with {problems} — they'll be at the top of your next brief"
    # A job that already told the user itself sets data["notified"] = True.
    if brief_ok:
        return None if (brief.data or {}).get("notified") else "Your brief is ready"
    if weekly_ok:
        return None if (weekly.data or {}).get("notified") else "Your weekly review is ready"
    return None


def _wall_clock() -> float:
    return time.time()


def _never_raises(notify: Notify, log) -> Notify:
    def send(title: str, message: str) -> None:
        try:
            notify(title, message)
        except Exception as exc:  # a notification must never break a run
            log.warning("notification failed: %s", exc)

    return send


def rotate_launchd_log(ws: Workspace) -> None:
    """launchd appends to logs/launchd.log forever; keep it to 1 MB plus 3 old copies."""
    current = ws.logs / "launchd.log"
    try:
        if current.stat().st_size < LAUNCHD_LOG_MAX:
            return
        for index in range(LAUNCHD_LOG_KEEP - 1, 0, -1):
            older = ws.logs / f"launchd.log.{index}"
            if older.exists():
                os.replace(str(older), str(ws.logs / f"launchd.log.{index + 1}"))
        os.replace(str(current), str(ws.logs / "launchd.log.1"))
    except OSError:
        pass


def _run_due(ws: Workspace, cfg: Config, runner: Runner, now: datetime, notify: Notify, log,
             runs: RunsLedger, due: List[schedule.DueJob], home: Any) -> Dict[str, JobResult]:
    began = _wall_clock()
    start_utc = schedule.to_utc(now)

    def stamp() -> datetime:  # ledger times follow `now` (so tests can fake the clock), in UTC maths
        return (start_utc + timedelta(seconds=max(0.0, _wall_clock() - began))).astimezone(now.tzinfo)

    run_started = stamp()
    env = job_env(ws, home)
    message = start_message(due, cfg, now)
    if message:
        notify(TITLE, message)
    results: Dict[str, JobResult] = {}
    for item in due:
        started = stamp()
        ctx = JobContext(ws=ws, config=cfg, runner=runner, now=now, runs=runs, log=log, notify=notify,
                         env=dict(env), since=item.since, results=dict(results))
        result = run_job(item.name, ctx, log)
        _record(runs, log, item.name, result, started, stamp())
        results[item.name] = result
        log.info("job %s: %s %s", item.name, result.status, result.summary or "")
    errors = sum(1 for r in results.values() if r.status == "error")
    summary = JobResult("error" if errors else "ok", summary=summarize(results),
                        data={"jobs": {name: r.status for name, r in results.items()}})
    _record(runs, log, "catchup", summary, run_started, stamp())
    message = end_message(results)
    if message:
        notify(TITLE, message)
    return results


def run_catchup(ws: Workspace, config: Config, runner: Runner, now: datetime, notify: Notify, log,
                only: Optional[str] = None, dry_run: bool = False, home: Any = None) -> int:
    """Scheduled mode returns 0 even when jobs fail; with `only`, 1 if that job errored."""
    notify = _never_raises(notify, log)
    cfg, problems = schedule.sanitize(config)
    for problem in problems:
        log.warning("settings: %s", problem)
    if only is not None and only not in jobs_pkg.JOB_ORDER:
        print(f"Unknown job: {only}. Choose from: {', '.join(jobs_pkg.JOB_ORDER)}", file=sys.stderr)
        return 2
    runs = RunsLedger(ws)
    if dry_run:
        print_plan(plan(cfg, runs, now, only), cfg, now)
        return 0
    with try_lock(ws.lock_file) as locked:
        if not locked:
            log.info("catch-up skipped: another run is in progress")
            if only:
                print("Another catch-up is running. Try again in a minute.")
            return 0
        rotate_launchd_log(ws)
        due = plan(cfg, runs, now, only)
        if not due:
            return 0
        results = _run_due(ws, cfg, runner, now, notify, log, runs, due, home)
    if only:
        result = results[only]
        print(f"{only}: {result.status}" + (f" — {result.summary}" if result.summary else ""))
        if result.detail:
            print(result.detail)
        return 1 if result.status == "error" else 0
    return 0


# ---------------------------------------------------------------- CLI


def _workspace(args: argparse.Namespace) -> Workspace:
    value = getattr(args, "workspace", None)
    return Workspace(Path(value)) if value else Workspace.default()


def register(subparsers) -> None:
    parser = subparsers.add_parser("catchup", help="run the scheduled jobs that are due (launchd runs this)")
    parser.add_argument("--dry-run", action="store_true", help="show what is due and why; run nothing")
    parser.add_argument("--job", choices=list(jobs_pkg.JOB_ORDER), help="run this one job now, even if not due")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    ws = _workspace(args)
    cfg, _ = schedule.sanitize(config_mod.load(ws))
    if args.dry_run:  # read-only: no folders, no log file
        log = logging.getLogger("brain.dry-run")
    else:
        ws.ensure_dirs()
        log = get_logger(ws)
    runner = Runner()
    now = clock.now(cfg.timezone)
    return run_catchup(ws, cfg, runner, now, make_notifier(runner, log), log,
                       only=args.job, dry_run=args.dry_run)
