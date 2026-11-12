"""PreToolUse hook for Claude Code: a second safety net under the deny rules.

Deny rules match command text as written, so `/full/path/gws gmail +send` or `sh -c '…'`
slip past them. This hook looks at the whole command string instead. It blocks:
- sending email (gws send/reply/forward, raw Gmail send API calls) and posting to Slack;
- reading stored secrets (Keychain password reads, credential files);
- edits to kit-owned files (they are replaced on update; the user's changes belong in
  user-owned files);
- destructive commands: recursive `rm`, `sudo`, `pkill`, `killall`, `git clean`,
  `git reset --hard` and force-pushes (rules in destructive.py).

It is defence in depth, not a sandbox: a determined agent could still get around it.
Exit code 2 blocks the call and shows stderr to the agent; anything unexpected allows the
call (a broken guard must never break the user's session).
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
from pathlib import Path
from typing import Iterable, List, Optional, Set

from . import destructive

SHELLS = {"sh", "bash", "zsh", "dash", "ksh", "fish"}
SHELL_C_FLAG = re.compile(r"^-[a-zA-Z]*c[a-zA-Z]*$")
SECRET_SUBCOMMANDS = {"find-generic-password", "find-internet-password", "dump-keychain", "export",
                      "find-certificate", "export-keychain"}

URL_PATTERNS = [
    (re.compile(r"gmail\.googleapis\.com/\S*/(messages|drafts)/send\b"), "sending email is turned off in the brain"),
    (re.compile(r"slack\.com/api/(chat\.(postMessage|update|delete|scheduleMessage|meMessage)"
                r"|files\.(upload|completeUploadExternal)|reactions\.add|conversations\.(invite|kick))\b"),
     "posting to Slack is turned off in the brain"),
]

SECRET_PATTERNS = [
    (re.compile(r"\.config/gws/(credentials|client_secret|token)"), "Google credential files are off limits"),
    (re.compile(r"\.claude/\.credentials\.json"), "Claude credential files are off limits"),
    (re.compile(r"\.codex/auth\.json"), "Codex credential files are off limits"),
    (re.compile(r"Library/Keychains"), "the Keychain folder is off limits"),
]

SEND_REASON = "sending email is turned off in the brain"
KEYCHAIN_REASON = "reading saved passwords is blocked; use `brain secret check <name>`"
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
READ_TOOLS = {"Read"}
HER_FILES_IN_KIT_AREA = {".kit/config.json"}

FIX_HINT = ("Make a draft instead (`brain draft email …`) and let the user send it, or show the "
            "Slack reply in chat for them to paste.")


def _normalise(command: str) -> str:
    """Join line continuations and turn substitutions/subshells into command breaks."""
    text = re.sub(r"\\\r?\n", " ", command)
    for opener in ("$(", "`", "(", ")"):
        text = text.replace(opener, " ; ")
    return text


def _segments(command: str) -> List[str]:
    return [seg for seg in re.split(r"\|\||&&|[;|&\n]", _normalise(command)) if seg.strip()]


def _tokens(segment: str) -> List[str]:
    try:
        return shlex.split(segment, posix=True)
    except ValueError:  # unbalanced quotes: fall back to plain words (still checked)
        return [word.strip("'\"") for word in segment.split()]


def _base(token: str) -> str:
    return os.path.basename(token)


def _check_tokens(tokens: List[str], depth: int) -> Optional[str]:
    from .gws import is_blocked  # shared rules with the shim

    for index, token in enumerate(tokens):
        name = _base(token)
        rest = tokens[index + 1:]
        if name in SHELLS and depth < 3:
            flag = next((t for t in rest if SHELL_C_FLAG.match(t)), None)  # `-c`, `-lc`, `-ic` …
            inner = rest[rest.index(flag) + 1:] if flag else []
            if inner:
                found = check_command(" ".join(inner), depth + 1)
                if found:
                    return found
        if name == "eval" and rest and depth < 3:
            found = check_command(" ".join(rest), depth + 1)
            if found:
                return found
        if name == "gws":
            if is_blocked(rest):
                return SEND_REASON
            if "xargs" in [_base(t) for t in tokens[:index]] and "gmail" in rest:
                return "passing arguments into gws through xargs is blocked; run gws directly"
        if name == "security":
            options = [t for t in rest if t.startswith("-")]
            words = [t for t in rest if not t.startswith("-")]
            if "-i" in options or (not words and "-i" in rest):
                return KEYCHAIN_REASON
            if words and words[0] in SECRET_SUBCOMMANDS:
                return KEYCHAIN_REASON
        if name == "brain" and "_apply" in rest:
            return "`brain _apply` is internal; use `brain update`"
        if token == "_apply" and any(_base(t) == "brain" for t in tokens[:index]):
            return "`brain _apply` is internal; use `brain update`"
        found = destructive.check(name, rest)
        if found:
            return found
    return None


def check_command(command: str, depth: int = 0) -> Optional[str]:
    """A reason to block this shell command, or None."""
    for pattern, reason in URL_PATTERNS:
        if pattern.search(command):
            return f"Blocked by your brain's safety rules: {reason}. {FIX_HINT}"
    for pattern, reason in SECRET_PATTERNS:
        if pattern.search(command):
            return f"Blocked by your brain's safety rules: {reason}."
    for segment in _segments(command):
        reason = _check_tokens(_tokens(segment), depth)
        if reason and reason.startswith("Blocked"):  # already a full message (`sh -c`, `eval`)
            return reason
        if reason:
            hint = f" {FIX_HINT}" if reason == SEND_REASON else ""
            if reason in destructive.DELETE_REASONS:
                hint = f" {destructive.TRASH_HINT}"
            return f"Blocked by your brain's safety rules: {reason}.{hint}"
    return None


def check_read(path: str) -> Optional[str]:
    for pattern, reason in SECRET_PATTERNS:
        if pattern.search(path):
            return f"Blocked by your brain's safety rules: {reason}."
    return None


def kit_files(project_dir: Path) -> Set[str]:
    try:
        data = json.loads((project_dir / ".kit" / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    return set((data.get("kit_files") or {}).keys())


def check_edit(path: str, project_dir: Path, owned: Iterable[str]) -> Optional[str]:
    try:
        rel = Path(path).expanduser().resolve().relative_to(project_dir.resolve()).as_posix()
    except (ValueError, OSError):
        return None  # outside the brain folder: not ours to police
    if rel in HER_FILES_IN_KIT_AREA:
        return None
    if rel in set(owned):
        return ("Blocked: this is a kit file, replaced on every update. Put the change in the "
                "user's own files instead (me.md, strategy.md, people.md, voice.md, a "
                "`.claude/skills/my-*` skill, or `.claude/settings.local.json`).")
    return None


def decide(payload: dict, project_dir: Path) -> Optional[str]:
    tool = payload.get("tool_name") or ""
    tool_input = payload.get("tool_input") or {}
    if tool == "Bash":
        return check_command(str(tool_input.get("command") or ""))
    if tool in READ_TOOLS:
        return check_read(str(tool_input.get("file_path") or ""))
    if tool in EDIT_TOOLS:
        path = str(tool_input.get("file_path") or tool_input.get("notebook_path") or "")
        return check_edit(path, project_dir, kit_files(project_dir)) if path else None
    return None


def main(stdin=None, environ=None) -> int:
    stdin = stdin or sys.stdin
    environ = environ if environ is not None else os.environ
    try:
        payload = json.loads(stdin.read() or "{}")
        project_dir = Path(environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or ".")
        reason = decide(payload, project_dir)
    except Exception:  # never break the session because the guard failed
        return 0
    if reason:
        sys.stderr.write(reason + "\n")
        return 2
    return 0
