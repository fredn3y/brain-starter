"""`brain report`: a redacted diagnostics zip the user can send to their consultant.

Only versions, settings, check results, recent run lines and log tails go in, each passed
through `redact`. Briefs, tasks, Slack ledgers and the user's own notes never go in: the zip
is built from ALLOWED_FILES only.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from . import clock, kit_version
from . import config as config_mod
from .config import Config
from .paths import Workspace
from .runner import Result, Runner
from .runs import RunsLedger

ALLOWED_FILES = ("summary.txt", "doctor.json", "runs.jsonl", "brain.log", "launchd.log", "config.json")
LOG_TAIL_LINES = 500
RUNS_TAIL = 100

_SECRET_PATTERNS = [
    (re.compile(r"-----BEGIN [A-Z ]+-----[\s\S]+?-----END [A-Z ]+-----"), "<pem>"),
    (re.compile(r"xox[abposr]-[A-Za-z0-9-]+"), "<slack-token>"),
    (re.compile(r"ya29\.[\w.-]+"), "<google-token>"),
    (re.compile(r"sk-[A-Za-z0-9_-]{16,}"), "<api-key>"),
]
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_USER_DIR = re.compile(r"/Users/[^/\s:;,'\"<>]+")
_LONG_RUN = re.compile(r"[A-Za-z0-9+/=_-]{32,}")


def redact_secrets(text: str) -> str:
    """Replace known token shapes (PEM blocks, Slack, Google, sk- keys)."""
    for pattern, label in _SECRET_PATTERNS:
        text = pattern.sub(label, text)
    return text


def redact(text: Any, home: Optional[str] = None) -> str:
    """Tokens, emails, home folders and any 32+ character key-like run become placeholders."""
    out = redact_secrets(str(text))
    out = _EMAIL.sub("<email>", out)
    home_text = str(home).rstrip("/") if home else ""
    if home_text:
        out = out.replace(home_text, "~")
    out = _USER_DIR.sub("/Users/<user>", out)
    return _LONG_RUN.sub("<redacted>", out)


def _redact_value(value: Any, home: Optional[str]) -> Any:
    if isinstance(value, str):
        return redact(value, home)
    if isinstance(value, list):
        return [_redact_value(item, home) for item in value]
    if isinstance(value, dict):
        return {redact(key, home): _redact_value(item, home) for key, item in value.items()}
    return value


# ---------------------------------------------------------------- report parts


def _first_line(result: Result) -> str:
    lines = (result.stdout or result.stderr or "").strip().splitlines()
    return lines[0].strip() if lines else ""


def _tool_version(runner: Runner, argv: List[str], env: Optional[Dict[str, str]]) -> str:
    result = runner.run(argv, env=env, timeout=30)
    if result.returncode == 127:
        return "not installed"
    if not result.ok:
        return f"error (exit {result.returncode})"
    return _first_line(result)[:120] or "unknown"


def _summary(ws: Workspace, config: Config, runner: Runner, now: datetime, platform: str,
             env: Optional[Dict[str, str]]) -> str:
    if platform == "darwin":
        mac = _tool_version(runner, ["sw_vers", "-productVersion"], env)
    else:
        mac = f"not a Mac ({platform})"
    local = now.astimezone(clock.tz(config.timezone))
    lines = [
        "brain report",
        f"created: {local.strftime('%Y-%m-%d %H:%M')} ({config.timezone})",
        f"kit: {kit_version()}",
        f"macOS: {mac}",
        f"cpu: {_tool_version(runner, ['uname', '-m'], env)}",
        f"python: {sys.version.split()[0]} ({sys.executable})",
        f"claude: {_tool_version(runner, ['claude', '--version'], env)}",
        f"codex: {_tool_version(runner, ['codex', '--version'], env)}",
        f"gws: {_tool_version(runner, ['gws', '--version'], env)}",
        f"workspace: {ws.root}",
    ]
    return "\n".join(lines) + "\n"


def _doctor_json(ws: Workspace, config: Config, runner: Runner, platform: str,
                 env: Optional[Dict[str, str]], now: datetime, home: str) -> str:
    try:
        from .doctor import run_checks

        checks = [c.to_dict() for c in run_checks(ws, config, runner, quick=False, platform=platform,
                                                   env=env, now=now, home=home)]
    except Exception as exc:  # the report must still be made
        checks = [{"name": "doctor", "status": "warn",
                   "message": f"doctor could not run: {type(exc).__name__}: {exc}", "fix": ""}]
    for item in checks:
        for key in ("message", "fix"):
            item[key] = redact(item.get(key) or "", home)
    return json.dumps(checks, indent=2, ensure_ascii=False) + "\n"


def _runs_tail(ws: Workspace, home: str) -> str:
    lines = []
    for row in RunsLedger(ws).recent(RUNS_TAIL):
        clean = dict(row)
        for key in ("summary", "detail"):
            if isinstance(clean.get(key), str):
                clean[key] = redact(clean[key], home)
        clean["data"] = _redact_value(clean.get("data") or {}, home)
        lines.append(json.dumps(clean, ensure_ascii=False))
    return "".join(line + "\n" for line in lines)


def _tail(paths: Iterable[Path], count: int) -> List[str]:
    """Last `count` lines across `paths` (oldest file first, e.g. brain.log.1 then brain.log)."""
    lines: List[str] = []
    for path in paths:
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                lines.extend(handle.read().splitlines())
        except OSError:
            continue
    return lines[-count:]


def _log_tail(paths: Iterable[Path], home: str) -> str:
    return "".join(redact(line, home) + "\n" for line in _tail(paths, LOG_TAIL_LINES))


def make_report(ws: Workspace, config: Config, runner: Runner, now: datetime,
                platform: Optional[str] = None, home: Optional[str] = None,
                env: Optional[Dict[str, str]] = None) -> Path:
    """Write `reports/brain-report-YYYYMMDD-HHMM.zip` and return its path."""
    from .launchagent import job_env

    platform = platform or sys.platform
    home_dir = str(home) if home else str(Path.home())
    env = env if env is not None else job_env(ws, home_dir)
    parts = {
        "summary.txt": redact(_summary(ws, config, runner, now, platform, env), home_dir),
        "doctor.json": _doctor_json(ws, config, runner, platform, env, now, home_dir),
        "runs.jsonl": _runs_tail(ws, home_dir),
        "brain.log": _log_tail([ws.logs / "brain.log.1", ws.logs / "brain.log"], home_dir),
        "launchd.log": _log_tail([ws.logs / "launchd.log.1", ws.logs / "launchd.log"], home_dir),
        "config.json": redact(json.dumps(config.to_dict(), indent=2, ensure_ascii=False), home_dir) + "\n",
    }
    ws.reports.mkdir(parents=True, exist_ok=True)
    local = now.astimezone(clock.tz(config.timezone))
    path = ws.reports / f"brain-report-{local.strftime('%Y%m%d-%H%M')}.zip"
    tmp = path.with_name(path.name + ".tmp")
    with zipfile.ZipFile(str(tmp), "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in ALLOWED_FILES:
            archive.writestr(name, parts[name])
    os.replace(str(tmp), str(path))
    if platform == "darwin":
        runner.run(["open", "-R", str(path)], timeout=15)  # show it in Finder; failure is fine
    return path


# ---------------------------------------------------------------- CLI


def _workspace(args: argparse.Namespace) -> Workspace:
    value = getattr(args, "workspace", None)
    return Workspace(Path(value)) if value else Workspace.default()


def _runner() -> Runner:
    return Runner()


def _platform() -> str:
    return sys.platform


def register(subparsers) -> None:
    parser = subparsers.add_parser("report", help="make a diagnostics file to send to your consultant")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    from .schedule import sanitize

    ws = _workspace(args)
    cfg, _ = sanitize(config_mod.load(ws))
    path = make_report(ws, cfg, _runner(), clock.now(cfg.timezone), platform=_platform())
    print(path)
    print("Send this file to your consultant so they can see what's going on.")
    return 0
