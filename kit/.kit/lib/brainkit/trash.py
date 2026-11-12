"""`brain trash <path>...`: move files or folders to the user's Trash instead of deleting them.

The safety rules block `rm -r` and friends; this is the safe way to remove something. It only
ever moves items into `~/.Trash` (the user can drag them back), never deletes, and refuses:
- anything that doesn't exist, or sits outside the user's home folder;
- the home folder itself, the Trash or anything already in it;
- the brain folder itself (or a folder holding it), `.kit/`, and kit-owned files or folders
  that hold them (they come back on every update anyway).
A name already in the Trash gets a number (`notes 2.txt`). Items moved this way have no
Finder "Put Back"; drag them back instead. Mac only: elsewhere it refuses and moves nothing.
"""

from __future__ import annotations

import argparse
import errno
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Optional, Set, Tuple

from .paths import Workspace

ALWAYS_KIT = {"AGENTS.md", "CLAUDE.md", ".claude/settings.json"}  # even with no manifest
MAX_NUMBER = 999


def _inside(path: Path, folder: Path) -> bool:
    return path == folder or folder in path.parents


def _kit_files(ws: Workspace) -> Set[str]:
    from .guard import kit_files  # the same list the edit guard uses

    return set(kit_files(ws.root)) | ALWAYS_KIT


def _locate(raw: str, cwd: Path) -> Path:
    """Absolute path to the item itself: parent folders resolved, a final link left as a link."""
    location = Path(os.path.abspath(os.path.join(str(cwd), os.path.expanduser(raw))))
    return Path(os.path.realpath(str(location.parent))) / location.name


def refusal(target: Path, home: Path, brain: Path, kit: Iterable[str]) -> Optional[str]:
    """Why `target` must not be trashed, or None. All paths are absolute and resolved."""
    trash_dir = home / ".Trash"
    if target == home:
        return "it is the home folder"
    if not _inside(target, home):
        return "it is outside the home folder"
    if _inside(target, trash_dir):
        return "it is already in the Trash"
    if target == brain:
        return "it is the brain folder"
    if _inside(brain, target):
        return "it holds the brain folder"
    if _inside(target, brain):
        rel = target.relative_to(brain).as_posix()
        if rel == ".kit" or rel.startswith(".kit/"):
            return "it is part of the brain kit"
        for kit_file in kit:
            if kit_file == rel or kit_file.startswith(rel + "/"):
                return "it is part of the brain kit"
    return None


def _exists(path: Path) -> Optional[bool]:
    """True/False, or None when the Mac won't let us look."""
    try:
        os.lstat(str(path))
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return None


def free_name(trash_dir: Path, name: str, now: Optional[datetime] = None) -> str:
    """A name not yet used in the Trash: `name`, then `stem 2.ext`, `stem 3.ext`, …"""
    stem, suffix = Path(name).stem, Path(name).suffix  # `.env` has no suffix
    candidates = [name] + [f"{stem} {n}{suffix}" for n in range(2, MAX_NUMBER + 1)]
    for candidate in candidates:
        found = _exists(trash_dir / candidate)
        if found is False:
            return candidate
        if found is None:
            break  # can't look inside the Trash: fall back to a time-stamped name
    stamp = (now or datetime.now()).strftime("%Y-%m-%d %H.%M.%S.%f")
    return f"{stem} {stamp}{suffix}"


def trash_one(raw: str, cwd: Path, home: Path, brain: Path, kit: Set[str]) -> Tuple[bool, str]:
    target = _locate(raw, cwd)
    if _exists(target) is False:
        return False, f"Not moved: nothing is at {raw}."
    why = refusal(target, home, brain, kit)
    if why:
        return False, f"Not moved: {raw} ({why})."
    trash_dir = home / ".Trash"
    try:
        trash_dir.mkdir(mode=0o700, exist_ok=True)
        name = free_name(trash_dir, target.name)
        os.rename(str(target), str(trash_dir / name))
    except OSError as exc:
        if exc.errno == errno.EXDEV:
            return False, f"Not moved: {raw} is on another drive. Ask the user to drag it to the Trash."
        return False, f"Not moved: {raw} (the Mac didn't allow it). Ask the user to drag it to the Trash."
    if name == target.name:
        return True, f"Moved {raw} to the Trash."
    return True, f'Moved {raw} to the Trash as "{name}" (that name was already there).'


def trash_paths(paths: List[str], ws: Workspace, home: Path, cwd: Optional[Path] = None,
                platform: Optional[str] = None) -> Tuple[int, List[str]]:
    """(exit code, one line per item). Exit 0 when everything moved, 1 if anything didn't."""
    if (platform or sys.platform) != "darwin":
        return 2, ["`brain trash` only works on a Mac. Nothing was moved."]
    home = Path(os.path.realpath(str(Path(home).expanduser())))
    brain = Path(os.path.realpath(str(ws.root)))
    kit = _kit_files(ws)
    where = Path(cwd) if cwd is not None else Path.cwd()
    code, lines = 0, []
    for raw in paths:
        moved, line = trash_one(raw, where, home, brain, kit)
        lines.append(line)
        if not moved:
            code = 1
    return code, lines


# ---------------------------------------------------------------- CLI


def _platform() -> str:
    return sys.platform


def _home() -> Path:
    return Path.home()


def register(subparsers) -> None:
    parser = subparsers.add_parser("trash", help="move files or folders to the Trash (never deletes)")
    parser.add_argument("paths", nargs="+", metavar="path")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    ws = Workspace(Path(args.workspace)) if getattr(args, "workspace", None) else Workspace.default()
    code, lines = trash_paths(args.paths, ws, _home(), platform=_platform())
    for line in lines:
        print(line, file=sys.stderr if code == 2 else sys.stdout)
    return code
