"""Destructive shell commands the PreToolUse hook blocks (used by guard.py).

The guard tokenises a command and calls `check(name, rest)` for every word, so a full path
(`/bin/rm`), a wrapper (`xargs rm -r`, `find … -exec rm -rf {} +`, `sudo rm`) or a shell inside a
shell is seen the same way. Blocked:
- `rm` whenever it is recursive (`-r`, `-R`, `--recursive`, alone or bundled with other letters);
- `sudo`, `pkill` and `killall` with anything after them (`man sudo` stays allowed);
- `git clean`, `git reset --hard`, and `git push` with `--force`, `-f`, `--force-with-lease` or
  a `+branch` refspec, wherever git's own options sit (`git -C dir push -f`).

A plain `rm file` stays allowed. Like the rest of the guard, this matches command text: another
language's delete call or an encoded command still gets through.
"""

from __future__ import annotations

from typing import List, Optional

TRASH_HINT = ("To remove something, move it to the Trash with `brain trash <path>` and tell the "
              "user what you moved.")

RM_REASON = "deleting folders for good is turned off in the brain"
SUDO_REASON = "running commands as the Mac's administrator (`sudo`) is turned off in the brain"
KILL_REASON = "stopping other programs is turned off in the brain; ask the user to quit them"
GIT_CLEAN_REASON = "`git clean` is turned off in the brain: it deletes files for good"
GIT_RESET_REASON = "`git reset --hard` is turned off in the brain: it throws away changes for good"
GIT_PUSH_REASON = "force-pushing is turned off in the brain: it can wipe out work saved online"

# Reasons whose block message also points to `brain trash`.
DELETE_REASONS = {RM_REASON, GIT_CLEAN_REASON}

# git's own options that take a separate value (`git -C dir status`).
GIT_VALUE_OPTIONS = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--super-prefix",
                     "--config-env", "--exec-path"}


def _rm_is_recursive(args: List[str]) -> bool:
    for arg in args:
        if arg == "--":
            return False
        if arg.startswith("--"):
            option = arg.split("=", 1)[0]
            if len(option) >= 3 and "--recursive".startswith(option):  # GNU takes `--rec` too
                return True
        elif arg.startswith("-") and len(arg) > 1 and ("r" in arg or "R" in arg):
            return True
    return False


def _git_subcommand(args: List[str]):
    """(subcommand, its arguments) after git's own options, or (None, [])."""
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in GIT_VALUE_OPTIONS:
            index += 2
            continue
        if arg.startswith("-"):
            index += 1
            continue
        return arg, args[index + 1:]
    return None, []


def _push_forces(args: List[str]) -> bool:
    for arg in args:
        if arg == "--":
            break
        if arg.startswith("--"):
            option = arg.split("=", 1)[0]
            if option in ("--force", "--force-with-lease"):
                return True
        elif arg.startswith("-") and len(arg) > 1:
            if "f" in arg[1:]:
                return True
        elif arg.startswith("+") and len(arg) > 1:
            return True
    return False


def check(name: str, rest: List[str]) -> Optional[str]:
    """A reason to block the command word `name` (a base name) followed by `rest`, or None."""
    if name == "rm" and _rm_is_recursive(rest):
        return RM_REASON
    if name == "sudo" and rest:
        return SUDO_REASON
    if name in ("pkill", "killall") and rest:
        return KILL_REASON
    if name == "git":
        sub, args = _git_subcommand(rest)
        if sub == "clean":
            return GIT_CLEAN_REASON
        if sub == "reset" and "--hard" in args:
            return GIT_RESET_REASON
        if sub == "push" and _push_forces(args):
            return GIT_PUSH_REASON
    return None
