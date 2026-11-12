"""`brain google status|connect` and `brain draft email`: Google sign-in and Gmail drafts.

  brain google status                       who is signed in, which permissions are missing
  brain google connect [--client-file PATH] install the OAuth client file, sign in in the browser
  brain draft email --to … --subject … (--body-file F | --body TEXT) [--cc …] [--reply-to ID]

`brain google connect` first fetches the Google tool (gws) when it isn't installed yet: on a
memory-only brain, Google is an add-on and gws arrives with it.

`gws auth login` only prints the Google sign-in link, so `connect` streams its output, opens
the link with macOS `open` and waits for gws to finish. The link is shown in the terminal but
never logged (it carries a one-time state value). Client secrets are never printed.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Mapping, Optional, Tuple

from .gws import (RECONNECT, REQUIRED_SCOPES, SCOPE_LABELS, NOISE_PREFIXES, GwsError, auth_status,
                  create_draft, missing_scopes)
from .browser import open_url
from .paths import Workspace
from .report import redact_secrets
from .runner import Runner

CLIENT_PATTERN = "client_secret*.json"
CLIENT_KEYS = ("client_id", "client_secret", "project_id", "auth_uri", "token_uri")
LOGIN_TIMEOUT = 600
SIGN_IN_URL = re.compile(r"https://accounts\.google\.com/\S+")
NO_CLIENT_FILE = (
    "I couldn't find your Google sign-in file (client_secret….json) in Downloads or {inbox}. "
    "Google shows a client's secret only once, when it is created. In Google Cloud, open "
    "Google Auth Platform → Clients → your Desktop client → Add secret, download the JSON file, "
    "then run `brain google connect` again.")
DOWNLOADS_BLOCKED = ("I'm not allowed to look in your Downloads folder. Drag the client_secret….json "
                     "file into {inbox}, then run `brain google connect` again.")

Spawn = Callable[[List[str], Optional[Dict[str, str]], Callable[[str], None], float], int]


class ClientFileError(GwsError):
    """The OAuth client file is missing, unreadable or the wrong kind."""


def display(path: Path, home: Path) -> str:
    full, base = os.path.abspath(str(path)), os.path.abspath(str(home)).rstrip(os.sep)
    return "~" + full[len(base):] if full == base or full.startswith(base + os.sep) else full


def gws_config_dir(env: Optional[Mapping[str, str]], home: Path) -> Path:
    custom = (env or {}).get("GOOGLE_WORKSPACE_CLI_CONFIG_DIR")
    return Path(custom).expanduser() if custom else Path(home) / ".config" / "gws"


def find_client_file(home: Path, inbox: Path) -> Tuple[Optional[Path], bool]:
    """Newest client_secret*.json in Downloads or the brain's inbox that holds a Desktop client.
    Returns (path or None, whether a folder or file was off limits)."""
    blocked = False
    found: List[Tuple[float, str]] = []
    for folder in (Path(home) / "Downloads", inbox):
        try:
            names = os.listdir(str(folder))  # not Path.glob: it hides PermissionError
        except PermissionError:
            blocked = True
            continue
        except OSError:
            continue
        for name in fnmatch.filter(names, CLIENT_PATTERN):
            try:
                found.append((os.stat(str(folder / name)).st_mtime, str(folder / name)))
            except OSError:
                continue
    for _, name in sorted(found, reverse=True):
        try:
            data = json.loads(Path(name).read_text(encoding="utf-8"))
        except PermissionError:
            blocked = True
            continue
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and isinstance(data.get("installed"), dict):
            return Path(name), blocked
    return None, blocked


def check_client_file(path: Path) -> None:
    """Raise ClientFileError unless `path` is a Desktop-app OAuth client file. Never shows values."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ClientFileError(f"There's no file at {path}.") from None
    except PermissionError:
        raise ClientFileError(f"I'm not allowed to read {path.name}. Drag it into your brain's inbox "
                              "folder and run `brain google connect` again.") from None
    except (OSError, ValueError):
        raise ClientFileError(f"{path.name} isn't a readable Google sign-in file.") from None
    client = data.get("installed") if isinstance(data, dict) else None
    if not isinstance(client, dict):
        if isinstance(data, dict) and isinstance(data.get("web"), dict):
            raise ClientFileError("That file is for a “Web application” client. In Google Cloud, create a "
                                  "“Desktop app” client and download its JSON file instead.")
        raise ClientFileError(f"{path.name} isn't a Google sign-in file (it has no “installed” section).")
    missing = [key for key in CLIENT_KEYS if not client.get(key)]
    if missing:
        raise ClientFileError(f"{path.name} is missing {', '.join(missing)}. Download the client's JSON "
                              "file again from Google Cloud.")


def install_client_file(source: Path, config_dir: Path, now: datetime) -> Tuple[Path, Optional[Path], bool]:
    """Move the client file to <config_dir>/client_secret.json (mode 600). A different existing
    file is kept as client_secret.json.bak-<time>. Returns (dest, backup, source removed)."""
    content = source.read_bytes()
    config_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(str(config_dir), 0o700)
    dest = config_dir / "client_secret.json"
    if os.path.realpath(str(source)) == os.path.realpath(str(dest)):
        os.chmod(str(dest), 0o600)
        return dest, None, True
    backup = None
    if dest.exists() and dest.read_bytes() != content:
        backup = config_dir / f"client_secret.json.bak-{now.strftime('%Y%m%d-%H%M%S')}"
        os.replace(str(dest), str(backup))
        os.chmod(str(backup), 0o600)
    tmp = config_dir / ".client_secret.json.tmp"
    if tmp.exists():
        tmp.unlink()
    with os.fdopen(os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as handle:
        handle.write(content)
    os.replace(str(tmp), str(dest))
    os.chmod(str(dest), 0o600)
    try:
        source.unlink()
        removed = True
    except OSError:
        removed = False
    return dest, backup, removed


def spawn_lines(argv: List[str], env: Optional[Dict[str, str]], on_line: Callable[[str], None],
                timeout: float) -> int:
    """Run an interactive command (stdin stays on the terminal) and hand each output line to
    `on_line`. The one place that bypasses Runner, which can't stream output during sign-in."""
    import subprocess
    import threading

    try:
        proc = subprocess.Popen(argv, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace", bufsize=1)
    except OSError:
        return 127

    def pump() -> None:
        for line in proc.stdout:  # type: ignore[union-attr]
            on_line(line)

    reader = threading.Thread(target=pump, daemon=True)
    reader.start()
    try:
        code = proc.wait(timeout=timeout)
    except (subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        code = 124 if isinstance(exc, subprocess.TimeoutExpired) else 130
    reader.join(timeout=5)
    return code


def run_login(runner: Runner, env: Optional[Mapping[str, str]], spawn: Spawn = spawn_lines,
              platform: Optional[str] = None, out: Callable[[str], None] = print,
              timeout: float = LOGIN_TIMEOUT) -> int:
    """`gws auth login` with the brain's scopes. gws only prints the sign-in link, so this opens
    it (Chrome when installed) and keeps waiting until gws finishes."""
    argv = ["gws", "auth", "login", "--scopes", ",".join(REQUIRED_SCOPES)]
    opened: List[str] = []

    def on_line(line: str) -> None:
        text = line.rstrip("\r\n")
        match = SIGN_IN_URL.search(text)
        if match:
            if not opened:
                opened.append(match.group(0))
                out("Opening the Google sign-in page in your browser. Sign in with the Google "
                    "account you want the brain to use and allow access.")
                out("If no browser window opens, copy this link into your browser:")
                out(match.group(0))
                open_url(runner, match.group(0), platform=platform, home=(env or {}).get("HOME"))
            return
        if text.strip() and not text.strip().startswith(NOISE_PREFIXES) and "open this url" not in text.lower():
            out(redact_secrets(text))

    return spawn(argv, dict(env) if env else None, on_line, timeout)


def show_status(runner: Runner, env: Optional[Mapping[str, str]], out: Callable[[str], None] = print) -> int:
    try:
        status = auth_status(runner, env)
    except GwsError as exc:
        out(f"Google: not connected ({exc}).")
        return 1
    valid = status.get("token_valid") is True
    missing = missing_scopes(status)
    out(f"Google account: {status.get('user') or 'not signed in'}")
    out(f"Signed in: {'yes' if valid else 'no'}")
    out("Missing permissions: " + (", ".join(SCOPE_LABELS[s] for s in missing) if missing else "none"))
    if not valid or missing:
        out(f"To fix: {RECONNECT}.")
    return 0 if valid and not missing else 1


def ensure_gws(ws: Workspace, runner: Runner, home: Path, log,
               out: Callable[[str], None] = print) -> bool:
    """Install gws from tools.json when it's missing. True when gws is there afterwards."""
    from . import tools

    try:
        [entry] = tools.select(tools.load_tools(tools.tools_file(ws)), ["gws"])
    except tools.ToolError as exc:
        out(f"Couldn't find the Google tool in the tools list ({exc}).")
        return False
    if tools.tool_status(entry, runner, home).installed:
        return True
    out("Getting the Google tool (gws) first. This can take a minute ...")
    outcome = tools.install_tool(entry, runner, home, log)
    if outcome.ok:
        out("The Google tool is installed.")
        return True
    out(f"The Google tool didn't install ({outcome.message}).")
    out("Check the internet connection, then run `brain google connect` again.")
    return False


def connect(ws: Workspace, runner: Runner, env: Optional[Mapping[str, str]], home: Path,
            client_file: Optional[str] = None, spawn: Spawn = spawn_lines, platform: Optional[str] = None,
            out: Callable[[str], None] = print, now: Optional[datetime] = None,
            ensure_tool: Optional[Callable[[], bool]] = None) -> int:
    """`ensure_tool` (the CLI passes ensure_gws) runs first and stops here when it fails."""
    if ensure_tool is not None and not ensure_tool():
        return 1
    inbox = ws.root / "inbox"
    if client_file:
        source = Path(client_file).expanduser()
    else:
        found, blocked = find_client_file(home, inbox)
        if found is None:
            inbox.mkdir(parents=True, exist_ok=True)
            out((DOWNLOADS_BLOCKED if blocked else NO_CLIENT_FILE).format(inbox=display(inbox, home) + "/"))
            return 1
        source = found
    try:
        check_client_file(source)
    except ClientFileError as exc:
        out(str(exc))
        return 1
    dest, backup, removed = install_client_file(source, gws_config_dir(env, home), now or datetime.now())
    out(f"Saved your Google sign-in file as {display(dest, home)}"
        + (f" (the previous one is kept as {backup.name})." if backup else "."))
    if not removed:
        out(f"You can delete {display(source, home)} now.")
    code = run_login(runner, env, spawn=spawn, platform=platform, out=out)
    if code != 0:
        out("Google sign-in didn't finish" + (" in time" if code == 124 else f" (exit {code})")
            + ". Run `brain google connect` again when you're ready.")
        return 1
    out("")
    return show_status(runner, env, out)


# ---------------------------------------------------------------- CLI


def _workspace(args: argparse.Namespace) -> Workspace:
    value = getattr(args, "workspace", None)
    return Workspace(Path(value)) if value else Workspace.default()


def _runner() -> Runner:
    return Runner()


def _env(ws: Workspace) -> Optional[Dict[str, str]]:
    """The scheduled-run environment, so the CLI uses the guard shim and the same HOME/USER."""
    try:
        from .launchagent import job_env

        return job_env(ws)
    except Exception:
        return None


def _log(ws: Workspace, message: str) -> None:
    try:
        from .log import get_logger

        get_logger(ws).info(message)
    except Exception:
        pass


def register(subparsers) -> None:
    google = subparsers.add_parser("google", help="connect Google (Gmail, Calendar, Drive) and check it")
    actions = google.add_subparsers(dest="google_action", metavar="<action>")
    actions.add_parser("status", help="who is signed in and what's missing").set_defaults(func=cmd_status)
    p_connect = actions.add_parser("connect", help="get the Google tool if needed, then sign in with your browser")
    p_connect.add_argument("--client-file", metavar="PATH",
                           help="the client_secret….json file (default: newest in Downloads or ~/brain/inbox)")
    p_connect.set_defaults(func=cmd_connect)
    google.set_defaults(func=lambda args: _usage(google))

    draft = subparsers.add_parser("draft", help="save a draft for you to review (never sends)")
    kinds = draft.add_subparsers(dest="draft_kind", metavar="<kind>")
    p_email = kinds.add_parser("email", help="save an email draft in Gmail")
    p_email.add_argument("--to", required=True, help="recipient address(es), comma-separated")
    p_email.add_argument("--cc", help="cc address(es), comma-separated")
    p_email.add_argument("--subject", help="subject (optional with --reply-to)")
    body = p_email.add_mutually_exclusive_group(required=True)
    body.add_argument("--body-file", metavar="FILE", help="file with the message text ('-' for stdin)")
    body.add_argument("--body", metavar="TEXT", help="the message text")
    p_email.add_argument("--reply-to", metavar="MESSAGE_ID", help="Gmail message id to reply to (threads it)")
    p_email.set_defaults(func=cmd_draft_email)
    draft.set_defaults(func=lambda args: _usage(draft))


def _usage(parser: argparse.ArgumentParser) -> int:
    parser.print_help()
    return 2


def cmd_status(args: argparse.Namespace) -> int:
    return show_status(_runner(), _env(_workspace(args)))


def _logger(ws: Workspace):
    try:
        from .log import get_logger

        return get_logger(ws)
    except Exception:
        import logging

        return logging.getLogger("brain")


def cmd_connect(args: argparse.Namespace) -> int:
    ws = _workspace(args)
    runner, home = _runner(), Path.home()
    code = connect(ws, runner, _env(ws), home, client_file=args.client_file,
                   ensure_tool=lambda: ensure_gws(ws, runner, home, _logger(ws)))
    _log(ws, f"google connect: {'ok' if code == 0 else 'not finished'}")
    return code


def cmd_draft_email(args: argparse.Namespace) -> int:
    if not args.subject and not args.reply_to:
        print("Add --subject (or --reply-to MESSAGE_ID to answer an email).", file=sys.stderr)
        return 2
    text = args.body or ""
    if args.body_file:
        try:
            if args.body_file == "-":
                text = sys.stdin.read()
            else:
                text = Path(args.body_file).expanduser().read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            print(f"Couldn't read the body file: {getattr(exc, 'strerror', None) or exc}", file=sys.stderr)
            return 1
    ws = _workspace(args)
    try:
        draft = create_draft(_runner(), args.to, args.subject or "", text, cc=args.cc,
                             reply_to_message_id=args.reply_to, env=_env(ws))
    except (GwsError, ValueError) as exc:
        print(f"Couldn't save the draft: {exc}", file=sys.stderr)
        _log(ws, "draft email: failed")
        return 1
    _log(ws, "draft email: saved")
    print("Draft saved in Gmail (not sent). Open Gmail → Drafts to review and send.")
    print(f"Draft id: {draft['id']}")
    return 0
