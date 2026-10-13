"""`brain schedule install|uninstall|status`: the launchd job that runs `brain catchup`.

launchd starts the job at the brief time and at the top of every hour. If the Mac was asleep
at those times, launchd fires the job once on wake, and the catch-up runner handles the gap.
`caffeinate -i -s` keeps the Mac awake while a run is in progress.

Also defines the environment every scheduled run gets (`job_env`), so the catch-up runner,
`brain doctor` and the plist agree on PATH. USER and HOME are always set: gws can discard its
saved login when it can't decrypt it under a different environment (upstream bug #886).
"""

from __future__ import annotations

import argparse
import getpass
import os
import plistlib
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional

from . import config as config_mod
from .config import Config
from .paths import Workspace
from .runner import Runner

LABEL = "local.brain-starter.catchup"
CAFFEINATE = "/usr/bin/caffeinate"
SYSTEM_PYTHON = "/usr/bin/python3"
SYSTEM_PATH = ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin")
# Passed through to jobs when present (launchd sets most of these for agents). PATH, HOME,
# USER and BRAIN_HOME are always set explicitly.
PASS_THROUGH_ENV = ("LOGNAME", "SHELL", "TMPDIR", "LANG", "LC_ALL", "LC_CTYPE",
                    "BRAIN_GWS_REAL")  # BRAIN_GWS_REAL: where the real gws lives, if not ~/.local/bin
BOOTSTRAP_TRIES = 3


@dataclass
class Outcome:
    ok: bool
    message: str


def _absolute(ws: Workspace) -> Workspace:
    return Workspace(Path(os.path.abspath(str(ws.root))))


def standard_path(ws: Workspace, home: Any) -> str:
    """PATH for scheduled runs: the kit's bin first, then user and system tool folders."""
    entries = [str(ws.kit_bin), str(Path(str(home)) / ".local" / "bin")] + list(SYSTEM_PATH)
    return ":".join(entries)


def current_user() -> str:
    """The macOS short user name, or "" if it can't be found."""
    try:
        return getpass.getuser()
    except Exception:
        return ""


def job_env(ws: Workspace, home: Any = None, base: Optional[Mapping[str, str]] = None,
            user: Optional[str] = None) -> Dict[str, str]:
    """Environment for jobs and the commands they run (no secrets in here)."""
    home_dir = str(home) if home else str(Path.home())
    source = os.environ if base is None else base
    env = {key: source[key] for key in PASS_THROUGH_ENV if source.get(key)}
    env.update(PATH=standard_path(ws, home_dir), HOME=home_dir, BRAIN_HOME=str(ws.root))
    who = user or current_user()
    if who:
        env["USER"] = who
    return env


def python_path(exists: Callable[[str], bool] = os.path.exists, fallback: Optional[str] = None) -> str:
    """Apple's /usr/bin/python3 when present (Command Line Tools), else this interpreter."""
    return SYSTEM_PYTHON if exists(SYSTEM_PYTHON) else (fallback or sys.executable)


def plist_path(agents_dir: Any = None, home: Any = None) -> Path:
    base = Path(agents_dir) if agents_dir else Path(str(home or Path.home())) / "Library" / "LaunchAgents"
    return base / f"{LABEL}.plist"


def build_plist(ws: Workspace, python_path: str, config: Config, home: Any,
                user: Optional[str] = None) -> Dict[str, Any]:
    """The LaunchAgent definition (see docs/architecture.md, LaunchAgent)."""
    from .schedule import sanitize, parse_hhmm

    cfg, _ = sanitize(config)
    hour, minute = parse_hhmm(cfg.brief_time)
    root = _absolute(ws)
    log_file = str(root.logs / "launchd.log")
    env = {"PATH": standard_path(root, home), "HOME": str(home), "USER": user or current_user(),
           "BRAIN_HOME": str(root.root)}
    if os.environ.get("BRAIN_GWS_REAL"):  # a non-standard gws location must reach scheduled runs too
        env["BRAIN_GWS_REAL"] = os.environ["BRAIN_GWS_REAL"]
    return {
        "Label": LABEL,
        "ProgramArguments": [CAFFEINATE, "-i", "-s", python_path, str(root.kit_bin / "brain"), "catchup"],
        "StartCalendarInterval": [{"Hour": hour, "Minute": minute}, {"Minute": 0}],
        "RunAtLoad": True,
        "WorkingDirectory": str(root.root),
        "EnvironmentVariables": {key: value for key, value in env.items() if value},
        "StandardOutPath": log_file,
        "StandardErrorPath": log_file,
    }


def _uid(uid: Optional[int]) -> int:
    return os.getuid() if uid is None else int(uid)


def install(ws: Workspace, runner: Runner, config: Config, agents_dir: Any = None,
            uid: Optional[int] = None, home: Any = None, python: Optional[str] = None,
            user: Optional[str] = None, sleep: Callable[[float], None] = time.sleep) -> Outcome:
    """Write the plist and (re)load it into the user's launchd session."""
    home_dir = str(home) if home else str(Path.home())
    target = plist_path(agents_dir, home_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    _absolute(ws).logs.mkdir(parents=True, exist_ok=True)  # launchd won't create the log folder
    data = build_plist(ws, python or python_path(), config, home_dir, user=user)
    tmp = target.with_name(target.name + ".tmp")
    with open(str(tmp), "wb") as handle:
        plistlib.dump(data, handle)
    os.chmod(str(tmp), 0o644)
    os.replace(str(tmp), str(target))

    domain = f"gui/{_uid(uid)}"
    runner.run(["launchctl", "bootout", f"{domain}/{LABEL}"], timeout=30)  # fine if not loaded
    result = None
    for attempt in range(BOOTSTRAP_TRIES):
        result = runner.run(["launchctl", "bootstrap", domain, str(target)], timeout=30)
        if result.ok:
            return Outcome(True, f"Schedule installed: {target}")
        if attempt + 1 < BOOTSTRAP_TRIES:
            sleep(1.0)  # bootout can take a moment to finish
    detail = (result.stderr or result.stdout).strip()[:200] if result else ""
    code = result.returncode if result else "?"
    return Outcome(False, f"launchctl bootstrap failed (exit {code}): {detail}")


def uninstall(ws: Workspace, runner: Runner, agents_dir: Any = None, uid: Optional[int] = None,
              home: Any = None) -> Outcome:
    target = plist_path(agents_dir, home)
    runner.run(["launchctl", "bootout", f"gui/{_uid(uid)}/{LABEL}"], timeout=30)
    try:
        target.unlink()
    except FileNotFoundError:
        pass
    return Outcome(True, "Schedule removed.")


def status(ws: Workspace, runner: Runner, agents_dir: Any = None, uid: Optional[int] = None,
           home: Any = None) -> Dict[str, Any]:
    """{"loaded", "state", "last_exit_code", "plist", "plist_exists", "points_here"}."""
    target = plist_path(agents_dir, home)
    result = runner.run(["launchctl", "print", f"gui/{_uid(uid)}/{LABEL}"], timeout=30)
    info: Dict[str, Any] = {"loaded": result.ok, "state": None, "last_exit_code": None,
                            "plist": str(target), "plist_exists": target.exists(), "points_here": None}
    if result.ok:
        for line in result.stdout.splitlines():
            key, sep, value = line.strip().partition(" = ")
            if not sep:
                continue
            if key == "state" and info["state"] is None:
                info["state"] = value.strip()
            elif key == "last exit code" and info["last_exit_code"] is None:
                info["last_exit_code"] = value.strip()
    if info["plist_exists"]:
        try:
            with open(str(target), "rb") as handle:
                installed = plistlib.load(handle)
            info["points_here"] = installed.get("WorkingDirectory") == str(_absolute(ws).root)
        except Exception:
            info["points_here"] = None
    return info


# ---------------------------------------------------------------- CLI


def _workspace(args: argparse.Namespace) -> Workspace:
    value = getattr(args, "workspace", None)
    return Workspace(Path(value)) if value else Workspace.default()


def _runner() -> Runner:
    return Runner()


def _platform() -> str:
    return sys.platform


def register(subparsers) -> None:
    parser = subparsers.add_parser("schedule", help="turn the daily brief schedule on or off")
    actions = parser.add_subparsers(dest="schedule_action", metavar="<action>")
    actions.add_parser("install", help="run catch-up at the brief time and hourly").set_defaults(func=run)
    actions.add_parser("uninstall", help="stop the schedule").set_defaults(func=run)
    actions.add_parser("status", help="is the schedule loaded?").set_defaults(func=run)
    parser.set_defaults(func=lambda args: _usage(parser))


def _usage(parser: argparse.ArgumentParser) -> int:
    parser.print_help()
    return 2


def run(args: argparse.Namespace) -> int:
    if _platform() != "darwin":
        print("The schedule uses macOS launchd, so it only works on a Mac.", file=sys.stderr)
        return 2
    ws = _workspace(args)
    runner = _runner()
    action = args.schedule_action
    if action == "install":
        cfg = config_mod.load(ws)
        outcome = install(ws, runner, cfg)
        if outcome.ok:
            print(f"Scheduled: your brief runs at {cfg.brief_time} on workdays, with an hourly check.")
            return 0
        print(f"Couldn't install the schedule. {outcome.message}", file=sys.stderr)
        return 1
    if action == "uninstall":
        print(uninstall(ws, runner).message)
        return 0
    info = status(ws, runner)
    print(f"Loaded: {'yes' if info['loaded'] else 'no'}")
    if info["state"]:
        print(f"State: {info['state']}")
    if info["last_exit_code"]:
        print(f"Last exit code: {info['last_exit_code']}")
    print(f"Plist: {info['plist']}" + ("" if info["plist_exists"] else " (missing)"))
    return 0 if info["loaded"] else 1
