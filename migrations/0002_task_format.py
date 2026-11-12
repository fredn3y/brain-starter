"""Move task files to the shared task format (kit 0.3.0).

Old shape: frontmatter `created`/`updated`/`due`; `## Outcome`; `## Next` with lines tagged
`[ai]`, `[me]` or `[waiting]`; `## Log` with dated lines, newest first.
New shape: frontmatter `created_at`/`updated_at`/`due_at`/`completed_at`/`links`;
`## Outcome`, `## Pinned`, `## Threads / Open Loops` (`[ai]`, `[human]`, `[external]`),
`## Activity` with `### YYYY-MM-DD` entry headings.

For each `*.md` under tasks/active, tasks/later and tasks/archive:

- Old keys are renamed in place (the value is kept exactly); `completed_at` and `links: []` are
  added when missing. In tasks/archive an added `completed_at` takes the file's `updated` date,
  since that is when it was last changed; elsewhere it is "".
- `## Next` (or `## Next steps`) becomes `## Threads / Open Loops`; in that section a line's
  `[me]` tag becomes `[human]` and `[waiting]` becomes `[external]` (a top-level `*` bullet
  becomes `-`). Nothing else on the line changes. Notes inside `<!-- -->` are left alone.
- `## Log` (or `## History`, `## Updates`) becomes `## Activity`. If that section has no `###`
  headings yet, a `### YYYY-MM-DD` heading is added above each run of lines that start with the
  same date. Every old line stays, word for word and in its order, under the heading of its
  own date; lines before the first dated one stay at the top of the section.
- Missing sections are added empty: `## Pinned` straight after the Outcome section, and
  `## Outcome`, `## Threads / Open Loops`, `## Activity` where the order needs them.

No line is ever removed or reordered. Each file that changes is first copied, with its time
stamps, to .kit/state/migrations/0002/<same path>, and keeps its original modified time so
the weekly review doesn't count it as newly done. A file already in the new format is left as
it is, so running this twice is harmless. A file this can't read (no frontmatter block, not
UTF-8 text, a frontmatter line that isn't `key: value`, or nothing that looks like a task) is
left untouched and named in the result line.
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import List, Optional, Tuple

FOLDERS = ("active", "later", "archive")
BACKUP_DIR = Path(".kit") / "state" / "migrations" / "0002"
OLD_KEYS = (("created", "created_at"), ("updated", "updated_at"), ("due", "due_at"))
NEXT_NAMES = ("next", "next steps")
LOG_NAMES = ("log", "history", "updates")
TAG_LINE = re.compile(r"^(\s*)([-*])(\s+)\[(ai|human|external|me|waiting)\]", re.IGNORECASE)
NEW_TAG = {"ai": "ai", "human": "human", "external": "external", "me": "human", "waiting": "external"}
DATED_LINE = re.compile(r"^(?:[-*]\s+)?\**\s*(\d{4}-\d{2}-\d{2})\b")
KEY_LINE = re.compile(r"^([A-Za-z_][\w-]*)\s*:")


class Unreadable(Exception):
    """This file isn't a task file this migration understands."""


# ---------------------------------------------------------------- small helpers


def _heading(line: str) -> Optional[str]:
    match = re.match(r"^##\s+(.+?)\s*#*\s*$", line)
    return " ".join(match.group(1).lower().split()) if match else None


def _markup_mask(lines: List[str]) -> List[bool]:
    """True for lines inside an HTML note or a ``` code block (never changed or read)."""
    mask, comment, fence = [], False, False
    for line in lines:
        stripped = line.strip()
        if comment:
            mask.append(True)
            comment = "-->" not in stripped
        elif fence:
            mask.append(True)
            fence = not stripped.startswith("```")
        elif stripped.startswith("<!--"):
            mask.append(True)
            comment = "-->" not in stripped[4:]
        elif stripped.startswith("```"):
            mask.append(True)
            fence = True
        else:
            mask.append(False)
    return mask


def _find(lines: List[str], mask: List[bool], names, start: int = 0) -> Optional[int]:
    for index in range(start, len(lines)):
        if not mask[index] and _heading(lines[index]) in names:
            return index
    return None


def _section_end(lines: List[str], mask: List[bool], start: int) -> int:
    for index in range(start + 1, len(lines)):
        if not mask[index] and _heading(lines[index]) is not None:
            return index
    return len(lines)


def _insert(lines: List[str], at: int, block: List[str]) -> None:
    """Insert a heading block at `at`, with a blank line before it when needed."""
    if at > 0 and lines[at - 1].strip():
        block = [""] + block
    lines[at:at] = block


# ---------------------------------------------------------------- the conversion


def _frontmatter(lines: List[str], archived: bool) -> int:
    """Rename and add keys in place; returns the index of the closing `---`."""
    if not lines or lines[0].strip() != "---":
        raise Unreadable("no frontmatter")
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        raise Unreadable("frontmatter never closes")
    keys = {}
    for index in range(1, end):
        line = lines[index]
        if not line.strip() or line[:1].isspace() or line.startswith(("- ", "#")):
            continue
        match = KEY_LINE.match(line)
        if not match:
            raise Unreadable("a frontmatter line isn't key: value")
        keys.setdefault(match.group(1).lower(), index)
    for old, new in OLD_KEYS:
        if old in keys and new not in keys:
            index = keys.pop(old)
            lines[index] = new + lines[index][len(old):]
            keys[new] = index
    added = []
    if "completed_at" not in keys:
        when = ""
        if archived and "updated_at" in keys:
            value = lines[keys["updated_at"]].partition(":")[2].strip().strip("\"'")
            when = value if re.match(r"^\d{4}-\d{2}-\d{2}$", value) else ""
        added.append(f'completed_at: "{when}"')
    if "links" not in keys:
        added.append("links: []")
    lines[end:end] = added
    return end + len(added)


def _date_headings(lines: List[str], mask: List[bool], start: int) -> None:
    """Give an Activity section without `###` headings one heading per run of same-date lines."""
    end = _section_end(lines, mask, start)
    if any(lines[i].startswith("###") and not mask[i] for i in range(start + 1, end)):
        return
    current, index = None, start + 1
    while index < end:
        match = None if mask[index] else DATED_LINE.match(lines[index])
        if match and match.group(1) != current:
            current = match.group(1)
            block = [f"### {current}"]
            if lines[index - 1].strip():
                block = [""] + block
            lines[index:index] = block
            mask[index:index] = [False] * len(block)
            index += len(block)
            end += len(block)
        index += 1


def convert(text: str, archived: bool) -> str:
    """The new-format text (the same text when nothing needs changing)."""
    newline = "\r\n" if "\r\n" in text else "\n"
    bom = "﻿" if text.startswith("﻿") else ""
    lines = text[len(bom):].replace("\r\n", "\n").split("\n")
    body = _frontmatter(lines, archived) + 1
    mask = [True] * body + _markup_mask(lines[body:])
    names = [None if mask[i] else _heading(lines[i]) for i in range(len(lines))]
    tagged = [i for i in range(body, len(lines)) if not mask[i] and TAG_LINE.match(lines[i])
              and not TAG_LINE.match(lines[i]).group(1)]
    known = {"outcome", "pinned", "threads / open loops", "activity"} | set(NEXT_NAMES) | set(LOG_NAMES)
    if not tagged and not known.intersection(n for n in names if n):
        raise Unreadable("nothing that looks like a task")

    for index, name in enumerate(names):
        if name in NEXT_NAMES and _find(lines, mask, ("threads / open loops",)) is None:
            lines[index] = "## Threads / Open Loops"
        elif name in LOG_NAMES and _find(lines, mask, ("activity",)) is None:
            lines[index] = "## Activity"

    activity = _find(lines, mask, ("activity",), body)
    if activity is None:
        if lines[-1].strip():
            _insert(lines, len(lines), ["## Activity", ""])
        else:
            _insert(lines, len(lines) - 1, ["## Activity"])
        mask = [True] * body + _markup_mask(lines[body:])
        activity = _find(lines, mask, ("activity",), body)
    threads = _find(lines, mask, ("threads / open loops",), body)
    if threads is None:
        loose = [i for i in tagged if i < activity]
        _insert(lines, loose[0] if loose else activity, ["## Threads / Open Loops", ""])
        mask = [True] * body + _markup_mask(lines[body:])
        threads = _find(lines, mask, ("threads / open loops",), body)
    if _find(lines, mask, ("outcome",), body) is None:
        first = next(i for i in range(body, len(lines)) if not mask[i] and _heading(lines[i]))
        _insert(lines, first, ["## Outcome", ""])
        mask = [True] * body + _markup_mask(lines[body:])
    if _find(lines, mask, ("pinned",), body) is None:
        outcome = _find(lines, mask, ("outcome",), body)
        _insert(lines, _section_end(lines, mask, outcome), ["## Pinned", ""])
        mask = [True] * body + _markup_mask(lines[body:])

    threads = _find(lines, mask, ("threads / open loops",), body)
    for index in range(threads + 1, _section_end(lines, mask, threads)):
        match = None if mask[index] else TAG_LINE.match(lines[index])
        if match:
            indent, bullet, gap, tag = match.groups()
            bullet = bullet if indent else "-"
            lines[index] = f"{indent}{bullet}{gap}[{NEW_TAG[tag.lower()]}]" + lines[index][match.end():]
    _date_headings(lines, mask, _find(lines, mask, ("activity",), body))
    return bom + newline.join(lines)


# ---------------------------------------------------------------- files


def _backup(workspace: Path, path: Path) -> None:
    target = workspace / BACKUP_DIR / path.relative_to(workspace)
    target.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(str(workspace / BACKUP_DIR), 0o700)
    number = 1
    while target.exists():
        if target.read_bytes() == path.read_bytes():
            return
        number += 1
        target = target.with_name(f"{path.stem}-{number}{path.suffix}")
    shutil.copy2(str(path), str(target))


def _write_keeping_times(path: Path, text: str) -> None:
    info = path.stat()
    tmp = path.with_name(f".{path.name}.0002.tmp")
    with open(str(tmp), "w", encoding="utf-8", newline="") as handle:
        handle.write(text)
    os.chmod(str(tmp), info.st_mode & 0o7777)
    os.utime(str(tmp), ns=(info.st_atime_ns, info.st_mtime_ns))
    os.replace(str(tmp), str(path))


def task_files(workspace: Path) -> List[Path]:
    found = []
    for folder in FOLDERS:
        root = workspace / "tasks" / folder
        if not root.is_dir() or root.is_symlink():
            continue
        for path in sorted(root.rglob("*.md")):
            rel = path.relative_to(root).parts
            if any(part.startswith(".") for part in rel) or path.name.upper() == "TEMPLATE.MD":
                continue
            found.append(path)
    return found


def _inside(path: Path, folder: Path) -> bool:
    try:
        path.resolve().relative_to(folder.resolve())
    except (OSError, ValueError):
        return False
    return True


def migrate_file(workspace: Path, path: Path) -> Tuple[str, str]:
    """('converted' | 'unchanged' | 'skipped', reason)."""
    if path.is_symlink() or not path.is_file() or not _inside(path, workspace / "tasks"):
        return "skipped", "a link or not a plain file"
    try:
        text = path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return "skipped", "not readable UTF-8 text"
    archived = path.relative_to(workspace / "tasks").parts[0] == "archive"
    try:
        new = convert(text, archived)
    except Unreadable as exc:
        return "skipped", str(exc)
    if new == text:
        return "unchanged", ""
    _backup(workspace, path)
    _write_keeping_times(path, new)
    return "converted", ""


def migrate(workspace: Path) -> str:
    workspace = Path(workspace)
    counts = {"converted": 0, "unchanged": 0}
    skipped = []
    for path in task_files(workspace):
        status, _ = migrate_file(workspace, path)
        if status == "skipped":
            skipped.append(str(path.relative_to(workspace)))
        else:
            counts[status] += 1
    if not counts["converted"] and not skipped:
        return "task files: nothing to do"
    parts = []
    if counts["converted"]:
        parts.append(f"moved {counts['converted']} task file(s) to the new format "
                     f"(originals copied to {BACKUP_DIR.as_posix()}/)")
    if counts["unchanged"]:
        parts.append(f"{counts['unchanged']} already in the new format")
    if skipped:
        parts.append("left as they were, not readable as task files: " + ", ".join(skipped))
    return "task files: " + "; ".join(parts)
