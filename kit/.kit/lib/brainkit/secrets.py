"""`brain secret set|check|delete|list`: keys in the macOS Keychain, through `security`.

Keychain generic passwords: service `brain.<name>`, account = the macOS user name. Values never
appear in argv, output, logs or error messages. `store` sends the add command to `security -i`
on stdin, so the value doesn't show up in `ps`; `exists` never reads the value at all.

TO CONFIRM IN THE MAC DRY RUN:
- `security -i` reads commands from a non-terminal stdin and exits at end of input. Its exit
  status may not reflect a failed add, so `store` reads the item back to be sure.
- Items added by `security` can be read by `security` from a launchd job with no prompt.
- The `--gui` dialog (osascript `display dialog ... with hidden answer`) can appear when the
  command is started by the chat app, and Cancel gives a non-zero exit.
"""

from __future__ import annotations

import argparse
import getpass
import re
import sys
from pathlib import Path
from typing import Callable, Optional

from .paths import Workspace
from .runner import Runner

NAMES = {
    "slack": "Slack token",
    "airwallex-client-id": "Airwallex client ID",
    "airwallex-key": "Airwallex API key",
    # Fallback when a launchd job can't read Claude's own login; exported to the engine as
    # CLAUDE_CODE_OAUTH_TOKEN.
    "claude-oauth-token": "Claude login token",
}
SERVICE_PREFIX = "brain."
NOT_FOUND = 44  # `security` exit status when the item doesn't exist
VALUE_PATTERN = re.compile(r"^[A-Za-z0-9._:/+=-]{8,1024}$")
ACCOUNT_PATTERN = re.compile(r"^[A-Za-z0-9._@-]{1,64}$")
VALUE_RULE = "8 to 1024 characters: letters, digits and . _ : / + = - (no spaces or quotes)"


class SecretError(RuntimeError):
    """A Keychain operation failed. Messages never contain the value."""


def label(name: str) -> str:
    _check_name(name)
    return NAMES[name]


def _check_name(name: str) -> None:
    if name not in NAMES:
        raise SecretError(f"unknown secret name {name!r}; allowed: {', '.join(NAMES)}")


def service(name: str) -> str:
    _check_name(name)
    return SERVICE_PREFIX + name


def account(user: Optional[str] = None) -> str:
    value = user or getpass.getuser()
    if not ACCOUNT_PATTERN.match(value):
        raise SecretError("the macOS user name has unusual characters; can't name the Keychain item")
    return value


def validate(name: str, value: str) -> str:
    """The stripped value, or ValueError with a message that doesn't repeat the value."""
    _check_name(name)
    cleaned = value.strip() if isinstance(value, str) else ""
    if not VALUE_PATTERN.match(cleaned):
        raise ValueError(f"That doesn't look like a {NAMES[name]}. It should be {VALUE_RULE}.")
    return cleaned


def get(runner: Runner, name: str, user: Optional[str] = None) -> Optional[str]:
    """The stored value, or None when nothing is stored."""
    result = runner.run(["security", "find-generic-password", "-s", service(name), "-a", account(user), "-w"],
                        timeout=30)
    if result.returncode == NOT_FOUND:
        return None
    if not result.ok:
        raise SecretError(f"couldn't read the {NAMES[name]} from the Keychain (security exit {result.returncode})")
    return result.stdout.rstrip("\r\n") or None


def exists(runner: Runner, name: str, user: Optional[str] = None) -> bool:
    """Is something stored? Reads attributes only, never the value."""
    result = runner.run(["security", "find-generic-password", "-s", service(name), "-a", account(user)],
                        timeout=30)
    if result.returncode == NOT_FOUND:
        return False
    if not result.ok:
        raise SecretError(f"couldn't check the Keychain for the {NAMES[name]} (security exit {result.returncode})")
    return True


def store(runner: Runner, name: str, value: str, user: Optional[str] = None) -> None:
    """Save (or replace) the value. Raises ValueError (bad value) or SecretError."""
    cleaned = validate(name, value)
    command = f"add-generic-password -U -s '{service(name)}' -a '{account(user)}' -w '{cleaned}'\n"
    result = runner.run(["security", "-i"], input=command, timeout=30)
    if not result.ok:  # never echo security's output: it could include the command line
        raise SecretError(f"the Keychain didn't accept the {NAMES[name]} (security exit {result.returncode})")
    if get(runner, name, user) != cleaned:
        raise SecretError(f"the {NAMES[name]} couldn't be read back from the Keychain after saving")


def delete(runner: Runner, name: str, user: Optional[str] = None) -> bool:
    """True when something was deleted, False when nothing was stored."""
    result = runner.run(["security", "delete-generic-password", "-s", service(name), "-a", account(user)],
                        timeout=30)
    if result.returncode == NOT_FOUND:
        return False
    if not result.ok:
        raise SecretError(f"couldn't delete the {NAMES[name]} (security exit {result.returncode})")
    return True


def dialog_script(name: str) -> str:
    text = f"Paste your {label(name)} here. It is saved in your Mac Keychain."
    return (f'text returned of (display dialog "{text}" default answer "" with hidden answer '
            'buttons {"Cancel", "Save"} default button "Save" with title "Brain")')


def prompt_value(runner: Runner, name: str, gui: bool,
                 ask: Callable[[str], str] = getpass.getpass) -> Optional[str]:
    """Ask the user for the value: a hidden-input Mac dialog (gui) or a hidden terminal prompt.
    None when cancelled or empty."""
    if gui:
        result = runner.run(["osascript", "-e", dialog_script(name)], timeout=600)
        if not result.ok:
            return None
        return result.stdout.strip() or None
    try:
        value = ask(f"Paste your {label(name)} (it stays hidden), then press Return: ")
    except (EOFError, KeyboardInterrupt):
        return None
    return value.strip() or None


# ---------------------------------------------------------------- CLI


def _workspace(args: argparse.Namespace) -> Workspace:
    value = getattr(args, "workspace", None)
    return Workspace(Path(value)) if value else Workspace.default()


def _runner() -> Runner:
    return Runner()


def _platform() -> str:
    return sys.platform


def _stdin_is_terminal() -> bool:
    try:
        return sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


def _ask(prompt: str) -> str:
    return getpass.getpass(prompt)


def _log(args: argparse.Namespace, message: str) -> None:
    """Names and outcomes only: never a value."""
    try:
        from .log import get_logger

        get_logger(_workspace(args)).info(message)
    except Exception:
        pass


def register(subparsers) -> None:
    parser = subparsers.add_parser("secret", help="save, check or delete a key in your Mac Keychain")
    actions = parser.add_subparsers(dest="secret_action", metavar="<action>")
    names = list(NAMES)
    p_set = actions.add_parser("set", help="save a key (asks for it; never shown)")
    p_set.add_argument("name", choices=names)
    p_set.add_argument("--gui", action="store_true", help="ask with a private Mac dialog")
    p_set.set_defaults(func=cmd_set)
    p_check = actions.add_parser("check", help="is this key saved?")
    p_check.add_argument("name", choices=names)
    p_check.set_defaults(func=cmd_check)
    p_delete = actions.add_parser("delete", help="remove a saved key")
    p_delete.add_argument("name", choices=names)
    p_delete.set_defaults(func=cmd_delete)
    actions.add_parser("list", help="which keys are saved").set_defaults(func=cmd_list)
    parser.set_defaults(func=lambda args: _usage(parser))


def _usage(parser: argparse.ArgumentParser) -> int:
    parser.print_help()
    return 2


def _needs_mac() -> bool:
    if _platform() == "darwin":
        return False
    print("Keys are kept in the Mac Keychain, so this only works on a Mac.", file=sys.stderr)
    return True


def cmd_set(args: argparse.Namespace) -> int:
    if _needs_mac():
        return 2
    if not args.gui and not _stdin_is_terminal():
        print("Run this in Terminal, or add --gui to get a private pop-up instead.", file=sys.stderr)
        return 2
    runner = _runner()
    value = prompt_value(runner, args.name, args.gui, ask=_ask)
    if value is None:
        print("Nothing saved.")
        return 1
    try:
        store(runner, args.name, value)
    except (ValueError, SecretError) as exc:
        print(str(exc), file=sys.stderr)
        _log(args, f"secret set {args.name}: failed")
        return 1
    _log(args, f"secret set {args.name}: saved")
    print(f"Saved: {args.name} (in your Mac Keychain)")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    if _needs_mac():
        return 2
    try:
        present = exists(_runner(), args.name)
    except SecretError as exc:
        print(f"{args.name}: unknown ({exc})", file=sys.stderr)
        return 1
    print(f"{args.name}: {'present' if present else 'missing'}")
    return 0 if present else 1


def cmd_delete(args: argparse.Namespace) -> int:
    if _needs_mac():
        return 2
    try:
        removed = delete(_runner(), args.name)
    except SecretError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    _log(args, f"secret delete {args.name}: {'deleted' if removed else 'was not stored'}")
    print(f"Deleted: {args.name}" if removed else f"{args.name} was not stored")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    if _needs_mac():
        return 2
    runner = _runner()
    for name in NAMES:
        try:
            state = "present" if exists(runner, name) else "missing"
        except SecretError:
            state = "unknown"
        print(f"{name}: {state}")
    return 0
