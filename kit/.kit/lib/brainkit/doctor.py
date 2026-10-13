"""`brain doctor [--json] [--quick]`: is everything the brain needs working?

One small function per check, each returning Check(s) with status ok|warn|fail|info and a
fix hint. A check that crashes becomes a "warn", so doctor itself never crashes. `quick` skips
network calls and the last-catch-up check (the health job runs doctor inside a catch-up).
Commands run with the scheduled-run environment, so doctor sees what launchd jobs see.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import shutil
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from . import clock, kit_version
from . import config as config_mod
from . import secrets as keychain
from .config import Config
from .launchagent import job_env
from .paths import Workspace
from .report import redact, redact_secrets
from .runner import Result, Runner
from .runs import RunsLedger
from .schedule import fmt_age, sanitize, to_utc

STATUSES = ("ok", "warn", "fail", "info")
SYMBOLS = {"ok": "✓", "warn": "!", "fail": "✗", "info": "·"}
MIN_MACOS = 13
MIN_PYTHON = (3, 9)
MIN_FREE_BYTES = 2 * 1024 ** 3
CATCHUP_STALE = timedelta(hours=26)
GOOGLE_SCOPES = ("gmail.readonly", "gmail.compose", "calendar.readonly", "drive.readonly")
# Broader grants that include a needed scope (Google OAuth scope descriptions, 2026-10-13).
BROADER_SCOPES = {
    "gmail.readonly": ("gmail.modify", "mail.google.com"),
    "gmail.compose": ("gmail.modify", "mail.google.com"),
    "calendar.readonly": ("calendar",),
    "drive.readonly": ("drive",),
}
T3_APP_PATHS = ["/Applications/T3 Code (Alpha).app", "~/Applications/T3 Code (Alpha).app"]
CODEX_RULES = "~/.codex/rules/brain-starter.rules"
CLAUDE_CONFIG = "~/.claude.json"
GOOGLE_FIX = "run `brain google connect`"
REPORT_FIX = "run `brain report` and send the file to your consultant"

disk_usage = shutil.disk_usage  # replaced in tests


@dataclass
class Check:
    name: str
    status: str  # ok | warn | fail | info
    message: str
    fix: str = ""

    def to_dict(self) -> Dict[str, str]:
        return asdict(self)


@dataclass
class Context:
    ws: Workspace
    config: Config
    runner: Runner
    quick: bool
    platform: str
    env: Optional[Dict[str, str]]
    now: datetime
    home: str
    settings_problems: List[str] = field(default_factory=list)

    @property
    def mac(self) -> bool:
        return self.platform == "darwin"

    def run(self, argv: Sequence[str], timeout: float = 30) -> Result:
        return self.runner.run(list(argv), env=self.env, timeout=timeout)

    def expand(self, path: str) -> Path:
        return Path(self.home + path[1:]) if path.startswith("~") else Path(path)

    def show(self, path: Any) -> str:
        """A path for messages: '~/brain' rather than the full home folder."""
        text = str(path)
        if text == self.home or text.startswith(self.home + "/"):
            return "~" + text[len(self.home):]
        return text

    @property
    def folder(self) -> str:
        return self.show(os.path.abspath(str(self.ws.root)))


# ---------------------------------------------------------------- helpers


def _first_line(result: Result) -> str:
    lines = (result.stdout or result.stderr or "").strip().splitlines()
    return lines[0].strip()[:120] if lines else ""


def _json_or_none(text: str) -> Any:
    try:
        return json.loads(text or "")
    except ValueError:
        return None


def _field(obj: Any, key: str) -> Any:
    return obj.get(key) if isinstance(obj, dict) else getattr(obj, key, None)


def _scrub(text: str, secret: str = "") -> str:
    if secret:
        text = text.replace(secret, "<hidden>")
    return redact_secrets(text)[:200]


def _optional_module(name: str) -> Any:
    try:
        return importlib.import_module(f"{__package__}.{name}")
    except ImportError:
        return None


def run_tool(ctx: Context, name: str, args: Sequence[str], timeout: float = 30) -> Tuple[Result, str]:
    """Run `name args`; when `name` isn't on PATH, try ~/.local/bin/<name> (the native
    installers put claude and codex there without adding it to PATH). Returns (result, exe)."""
    result = ctx.run([name] + list(args), timeout)
    if result.returncode == 127:
        fallback = Path(ctx.home) / ".local" / "bin" / name
        if fallback.exists():
            return ctx.run([str(fallback)] + list(args), timeout), str(fallback)
    return result, name


# ---------------------------------------------------------------- system


def check_macos(ctx: Context) -> Check:
    if not ctx.mac:
        return Check("macos", "info", f"not a Mac ({ctx.platform}); Mac-only checks skipped")
    version = ctx.run(["sw_vers", "-productVersion"]).stdout.strip()
    try:
        major = int(version.split(".")[0])
    except ValueError:
        return Check("macos", "warn", "couldn't read the macOS version")
    if major < MIN_MACOS:
        return Check("macos", "fail", f"macOS {version} is too old (needs macOS {MIN_MACOS} or newer)",
                     "update macOS: System Settings → General → Software Update")
    return Check("macos", "ok", f"macOS {version}")


def check_cpu(ctx: Context) -> Check:
    arch = ctx.run(["uname", "-m"]).stdout.strip() or "unknown"
    kind = {"arm64": "Apple silicon", "x86_64": "Intel"}.get(arch) if ctx.mac else None
    return Check("cpu", "info", f"{kind} ({arch})" if kind else arch)


def check_command_line_tools(ctx: Context) -> Optional[Check]:
    if not ctx.mac:
        return None
    result = ctx.run(["xcode-select", "-p"])
    if result.ok and result.stdout.strip():
        return Check("command line tools", "ok", "installed")
    return Check("command line tools", "fail", "Apple's Command Line Tools aren't installed",
                 "run `xcode-select --install`")


def check_python(ctx: Context) -> Check:
    version = ".".join(str(part) for part in sys.version_info[:3])
    if tuple(sys.version_info[:2]) >= MIN_PYTHON:
        return Check("python", "ok", f"Python {version}")
    return Check("python", "fail", f"Python {version} is too old (needs 3.9 or newer)",
                 "run `xcode-select --install`")


def check_kit(ctx: Context) -> Check:
    version = kit_version()
    row = RunsLedger(ctx.ws).last("update_check") or {}
    data = row.get("data") or {}
    latest = data.get("latest")
    if data.get("update_available") and latest:
        notes = str(data.get("notes") or "").strip().splitlines()
        extra = f": {notes[0][:80]}" if notes else ""
        return Check("kit", "info", f"version {version}; version {latest} is available{extra}", "run `brain update`")
    return Check("kit", "ok", f"version {version}" + (" (up to date)" if latest else ""))


def check_folders(ctx: Context) -> Check:
    ws = ctx.ws
    if not ws.root.is_dir():
        return Check("folders", "fail", f"there's no brain folder at {ctx.folder}", "run the installer")
    wanted = {".kit": ws.kit, "tasks": ws.tasks, "briefs": ws.briefs, "ledgers": ws.ledgers, "logs": ws.logs}
    missing = [name for name, path in wanted.items() if not path.is_dir()]
    if missing:
        return Check("folders", "warn", "missing in the brain folder: " + ", ".join(missing),
                     "run the installer again (it keeps your files)")
    return Check("folders", "ok", f"{ctx.folder} is in place")


def check_settings(ctx: Context) -> Check:
    if ctx.settings_problems:
        return Check("settings", "warn", "; ".join(ctx.settings_problems),
                     "fix .kit/config.json (or ask your assistant to)")
    return Check("settings", "ok", f"brief at {ctx.config.brief_time} ({ctx.config.timezone})")


# ---------------------------------------------------------------- AI tools


def check_claude_login(runner: Runner, exe: str = "claude", env: Optional[Dict[str, str]] = None,
                       folder: str = "~/brain") -> Check:
    """Is Claude Code logged in? The only place that knows how to ask.

    `claude auth status` prints JSON and exits 0 when logged in, 1 when not (Claude Code CLI
    reference, checked 2026-10-13); a JSON `"loggedIn": false` also counts as logged out. Any
    other outcome (timeout, crash) is a warning: we couldn't tell either way.
    """
    fix = f"open Terminal, run `cd {folder} && claude`, log in with your work account, then type /exit"
    result = runner.run([exe, "auth", "status"], env=env, timeout=30)
    data = _json_or_none(result.stdout)
    if result.returncode == 1 or (isinstance(data, dict) and data.get("loggedIn") is False):
        return Check("claude login", "fail", "Claude Code isn't logged in", fix)
    if result.ok:
        return Check("claude login", "ok", "logged in")
    return Check("claude login", "warn", "couldn't confirm Claude login", fix)


def check_claude_trust(ctx: Context) -> Check:
    """Read-only look at ~/.claude.json: has the user accepted the trust dialog for the brain
    folder? Never writes the file."""
    message = "Claude hasn't been told to trust the brain folder yet"
    fix = f"run `cd {ctx.folder} && claude`, accept the question about trusting this folder, then /exit"
    try:
        data = json.loads(ctx.expand(CLAUDE_CONFIG).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return Check("claude trust", "warn", message, fix)
    except (OSError, ValueError):
        return Check("claude trust", "warn", f"couldn't read {CLAUDE_CONFIG}", fix)
    projects = data.get("projects") if isinstance(data, dict) else None
    if isinstance(projects, dict):
        for key in {os.path.abspath(str(ctx.ws.root)), os.path.realpath(str(ctx.ws.root))}:
            entry = projects.get(key)
            if isinstance(entry, dict) and entry.get("hasTrustDialogAccepted") is True:
                return Check("claude trust", "ok", "Claude trusts the brain folder")
    return Check("claude trust", "warn", message, fix)


def check_claude(ctx: Context) -> List[Check]:
    result, exe = run_tool(ctx, "claude", ["--version"])
    if not result.ok:
        return [Check("claude", "fail", "Claude Code isn't installed (or can't be found)",
                      "run `brain tools install`")]
    return [Check("claude", "ok", f"installed ({_first_line(result)})"),
            check_claude_login(ctx.runner, exe, _engine_env(ctx), ctx.folder),
            check_claude_trust(ctx)]


def _engine_env(ctx: Context) -> Optional[Dict[str, str]]:
    """The environment the scheduled brief gets: plus the saved Claude token, if there is one,
    so the login check matches what the brief run will see."""
    env = dict(ctx.env) if ctx.env else None
    try:
        from . import secrets
        token = secrets.get(ctx.runner, "claude-oauth-token")
    except Exception:  # Keychain trouble shows up in its own check
        token = None
    if token:
        env = dict(env or os.environ)
        env["CLAUDE_CODE_OAUTH_TOKEN"] = token
    return env


def check_codex(ctx: Context) -> List[Check]:
    """Codex is optional: never a "fail". `codex login status` exits 0 when logged in."""
    result, exe = run_tool(ctx, "codex", ["--version"])
    if not result.ok:
        return [Check("codex", "info", "Codex isn't installed (optional)",
                      "to add it, run `brain tools install codex`")]
    checks = [Check("codex", "ok", f"installed ({_first_line(result)})")]
    if ctx.run([exe, "login", "status"]).ok:  # output not shown: it can mention a key
        checks.append(Check("codex login", "ok", "logged in"))
    else:
        checks.append(Check("codex login", "info", "Codex isn't logged in (optional)",
                            "run `codex login` in Terminal"))
    if ctx.expand(CODEX_RULES).is_file():
        checks.append(Check("codex safety rules", "ok", "in place"))
    else:
        checks.append(Check("codex safety rules", "warn", "Codex safety rules are missing",
                            "run `brain repair`"))
    return checks


# ---------------------------------------------------------------- connections


def _scope_name(scope: str) -> str:
    return scope.rstrip("/").rsplit("/", 1)[-1]


def check_google(ctx: Context) -> Check:
    version, exe = run_tool(ctx, "gws", ["--version"])
    if not version.ok:
        return Check("google", "fail", "the Google tool (gws) isn't installed", "run `brain tools install`")
    data = _json_or_none(ctx.run([exe, "auth", "status"]).stdout)
    if not isinstance(data, dict):
        return Check("google", "fail", "couldn't read the Google connection status", GOOGLE_FIX)
    if data.get("has_refresh_token") is not True:
        return Check("google", "fail", "Google isn't connected", GOOGLE_FIX)
    if data.get("token_valid") is not True:
        return Check("google", "fail", "the Google login has expired or been revoked", GOOGLE_FIX)
    granted = {_scope_name(s) for s in data.get("scopes") or [] if isinstance(s, str)}
    missing = [s for s in GOOGLE_SCOPES if not granted.intersection((s,) + BROADER_SCOPES.get(s, ()))]
    if missing:
        return Check("google", "fail", "Google is missing permissions: " + ", ".join(missing), GOOGLE_FIX)
    return Check("google", "ok", "connected (Gmail, Calendar, Drive)")


def check_slack(ctx: Context) -> Optional[Check]:
    if not ctx.mac:
        return None
    try:
        present = keychain.exists(ctx.runner, "slack")
    except keychain.SecretError as exc:
        return Check("slack", "warn", str(exc), REPORT_FIX)
    if not present:
        return Check("slack", "warn", "Slack isn't connected", "run `brain secret set slack --gui`")
    slack_api = None if ctx.quick else _optional_module("slack_api")
    if slack_api is None or not hasattr(slack_api, "auth_test"):
        return Check("slack", "ok", "token saved")
    try:
        token = keychain.get(ctx.runner, "slack") or ""
    except keychain.SecretError as exc:
        return Check("slack", "warn", str(exc), REPORT_FIX)
    try:
        answer = slack_api.auth_test(ctx.runner, token)
    except Exception as exc:
        return Check("slack", "warn", "couldn't confirm the Slack connection: " + _scrub(str(exc), token),
                     "check the internet connection, then run `brain doctor` again")
    if _field(answer, "ok") is False:
        error = _scrub(str(_field(answer, "error") or "rejected"), token)
        return Check("slack", "warn", f"Slack rejected the saved token ({error})",
                     "run `brain secret set slack --gui` with a new token")
    team, user = _field(answer, "team_id"), _field(answer, "user_id")
    return Check("slack", "ok", f"connected (team {team}, user {user})")


# ---------------------------------------------------------------- schedule and machine


def check_schedule(ctx: Context) -> Optional[Check]:
    if not ctx.mac:
        return None
    from .launchagent import status

    info = status(ctx.ws, ctx.runner, home=ctx.home)
    if not info["loaded"]:
        return Check("schedule", "warn", "the daily schedule isn't installed", "run `brain schedule install`")
    if info.get("points_here") is False:
        return Check("schedule", "warn", "the schedule points at a different brain folder",
                     "run `brain schedule install`")
    code = str(info.get("last_exit_code") or "")
    if code and not code.startswith("(") and code.split(":")[0].strip() != "0":
        return Check("schedule", "warn", f"the last scheduled run ended with exit code {code}", REPORT_FIX)
    state = info.get("state")
    return Check("schedule", "ok", "installed" + (f" ({state})" if state else ""))


def check_last_catchup(ctx: Context) -> Optional[Check]:
    if ctx.quick:  # inside a catch-up this check would only describe the gap being filled
        return None
    row = RunsLedger(ctx.ws).last("catchup", status=None) or {}
    finished = clock.parse_iso(row.get("finished_at"))
    now_utc = to_utc(ctx.now)
    workday = now_utc.astimezone(clock.tz(ctx.config.timezone)).isoweekday() in ctx.config.workdays
    fix = "it runs at the brief time, or as soon as you open your Mac; check `brain schedule status`"
    if finished is None:
        return Check("last catch-up", "warn" if workday else "info", "no catch-up has run yet", fix)
    age = now_utc - to_utc(finished)
    if age > CATCHUP_STALE and workday:
        return Check("last catch-up", "warn", f"the last catch-up was {fmt_age(age)} ago", fix)
    when = clock.fmt_local(finished, ctx.config.timezone)
    return Check("last catch-up", "ok", f"{when}: {row.get('summary') or row.get('status')}")


def check_disk(ctx: Context) -> Check:
    target = ctx.ws.root if ctx.ws.root.exists() else Path(ctx.home)
    free = disk_usage(str(target)).free
    gigabytes = free / 1024 ** 3
    if free < MIN_FREE_BYTES:
        return Check("disk", "warn", f"only {gigabytes:.1f} GB free",
                     "free up space: empty the Bin and remove large downloads")
    return Check("disk", "ok", f"{gigabytes:.0f} GB free")


def check_time_machine(ctx: Context) -> Optional[Check]:
    if not ctx.mac:
        return None
    result = ctx.run(["tmutil", "destinationinfo"])
    if "No destinations configured" in result.stdout + result.stderr:
        return Check("time machine", "warn", "Time Machine has no backup disk",
                     "set one up: System Settings → General → Time Machine")
    if result.ok and any(line.strip().startswith(("Name", "ID")) for line in result.stdout.splitlines()):
        return Check("time machine", "ok", "a backup destination is set up")
    return Check("time machine", "info", "couldn't read the Time Machine status")


def check_mdm(ctx: Context) -> Optional[Check]:
    if not ctx.mac:
        return None
    for line in ctx.run(["profiles", "status", "-type", "enrollment"]).stdout.splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip().lower() == "mdm enrollment":
            return Check("mdm", "info", f"MDM enrollment: {value.strip() or 'unknown'}")
    return Check("mdm", "info", "MDM enrollment: unknown")


def check_t3_app(ctx: Context) -> Optional[Check]:
    if not ctx.mac:
        return None
    if any(ctx.expand(path).exists() for path in T3_APP_PATHS):
        return Check("t3 app", "ok", "T3 Code is installed")
    return Check("t3 app", "info", "T3 Code (the chat window) isn't installed", "run `brain tools install t3`")


# ---------------------------------------------------------------- running


# (name, function name). Looked up at run time so tests can replace a check.
CHECKS = [
    ("macos", "check_macos"), ("cpu", "check_cpu"), ("command line tools", "check_command_line_tools"),
    ("python", "check_python"), ("kit", "check_kit"), ("folders", "check_folders"),
    ("settings", "check_settings"), ("claude", "check_claude"), ("codex", "check_codex"),
    ("google", "check_google"), ("slack", "check_slack"), ("schedule", "check_schedule"),
    ("last catch-up", "check_last_catchup"), ("disk", "check_disk"), ("time machine", "check_time_machine"),
    ("mdm", "check_mdm"), ("t3 app", "check_t3_app"),
]


def _guarded(name: str, func: Callable[[Context], Any], ctx: Context) -> List[Check]:
    try:
        out = func(ctx)
    except Exception as exc:  # one broken check must never crash doctor
        reason = redact(f"{type(exc).__name__}: {exc}")[:200]
        return [Check(name, "warn", f"this check crashed ({reason})", REPORT_FIX)]
    items = out if isinstance(out, list) else [out]
    return [item for item in items if isinstance(item, Check)]


def run_checks(ws: Workspace, config: Config, runner: Runner, quick: bool = False,
               platform: Optional[str] = None, env: Optional[Dict[str, str]] = None,
               now: Optional[datetime] = None, home: Any = None) -> List[Check]:
    cfg, problems = sanitize(config)
    home_dir = (str(home) if home else str(Path.home())).rstrip("/") or "/"
    ctx = Context(ws=ws, config=cfg, runner=runner, quick=quick, platform=platform or sys.platform,
                  env=env if env is not None else job_env(ws, home_dir),
                  now=now or clock.now(cfg.timezone), home=home_dir, settings_problems=problems)
    checks: List[Check] = []
    for name, func_name in CHECKS:
        checks.extend(_guarded(name, globals()[func_name], ctx))
    return checks


def format_line(check: Check) -> str:
    line = f"{SYMBOLS.get(check.status, '?')} {check.name}: {check.message}"
    if check.fix and check.status != "ok":
        line += f" — {check.fix}"
    return line


# ---------------------------------------------------------------- CLI


def _workspace(args: argparse.Namespace) -> Workspace:
    value = getattr(args, "workspace", None)
    return Workspace(Path(value)) if value else Workspace.default()


def _runner() -> Runner:
    return Runner()


def _platform() -> str:
    return sys.platform


def register(subparsers) -> None:
    parser = subparsers.add_parser("doctor", help="check that everything your brain needs is working")
    parser.add_argument("--json", action="store_true", help="print the checks as JSON (for agents)")
    parser.add_argument("--quick", action="store_true", help="skip the slower network checks")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    ws = _workspace(args)
    checks = run_checks(ws, config_mod.load(ws), _runner(), quick=args.quick, platform=_platform())
    if args.json:
        print(json.dumps([check.to_dict() for check in checks], indent=2, ensure_ascii=False))
    else:
        for check in checks:
            print(format_line(check))
    return 1 if any(check.status == "fail" for check in checks) else 0
