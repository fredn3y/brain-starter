"""Read task files (`tasks/<folder>/*.md`): frontmatter, threads, Pinned facts and Activity.

The one place the kit parses a task file. The format is the shared one (see `tasks/TEMPLATE.md`
and docs/architecture.md, "Task files"):

  frontmatter  title, priority, created_at, updated_at, due_at, completed_at, links
               (optional: project, blocker, comms)
  ## Outcome   ## Pinned   ## Threads / Open Loops   ## Activity

`parse_frontmatter` and `extract_threads` return the same shapes as the owner's task store
(`taskstore.parse_frontmatter` / `taskstore.extract_threads`), so the shared checker and brief
can later swap theirs in with little change. Where this reader differs, on purpose:

- It also reads the kit's OLD format (`created`/`updated`/`due`, `## Next` lines tagged
  `[ai]`/`[me]`/`[waiting]`, `## Log`), because `brain rollback` keeps a person's newer files
  and an update can stop half way. Old tags come back as the new ones (`human`, `external`)
  and old keys are copied to the new names by `task_meta`.
- It is forgiving about hand edits: a byte-order mark, Windows line endings, `*` bullets,
  upper-case tags and either kind of quote around a value. Keys are read in lower case.
- Lines inside `<!-- ... -->` notes are never threads.
- No retired `parked` tag and no owner-specific "approved" kind.
- Line numbers (`start`, `end`) count lines of the text after the mark and `\\r` are dropped.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterator, List, Optional, Tuple

THREADS_HEADING = "## Threads / Open Loops"
ACTIVITY_HEADING = "## Activity"
PINNED_HEADING = "## Pinned"
OUTCOME_HEADING = "## Outcome"
THREAD_TAGS = ("ai", "human", "external", "deferred")
OLD_TAGS = {"me": "human", "waiting": "external"}
OLD_KEYS = {"created": "created_at", "updated": "updated_at", "due": "due_at"}
# Old-format section names (lower case): the open steps, and the dated history.
OLD_NEXT_HEADINGS = ("next",)
OLD_LOG_HEADINGS = ("log", "activity", "history", "updates")
COMMS_LIST_KEYS = {"gmail_labels", "slack_channels"}

THREAD_CONTEXT_FIELDS = {
    "title": "title", "why": "why", "brief": "brief", "recommend": "recommend",
    "preview": "preview", "context": "context", "options": "options", "if-skipped": "if_skipped",
    "you-asked": "you_asked", "source": "source", "draft": "draft", "to": "to",
    "recipient": "recipient", "recipients": "recipients", "channel": "channel", "method": "method",
    "medium": "medium", "snooze-until": "snooze_until", "park-until": "park_until",
    "defer-hash": "defer_hash", "follow-up": "follow_up",
}
THREAD_CONTEXT_RE = re.compile(
    r"^(Title|Why|Brief|Recommend|Preview|Context|Options|If-Skipped|You-Asked|Source|Draft|"
    r"To|Recipient|Recipients|Channel|Method|Medium|"
    r"Snooze-Until|Park-Until|Defer-Hash|Follow-Up):\s*(.*)$",
    re.IGNORECASE,
)
# Fields that ride along on a thread only when present (the rest are always there).
OPTIONAL_FIELDS = ("to", "recipient", "recipients", "channel", "method", "medium", "context",
                   "options", "if_skipped", "you_asked", "source", "draft", "follow_up")
TAG_RE = re.compile(r"^[-*]\s+\[(ai|human|external|me|waiting)\]\s*(.*)", re.IGNORECASE)
DEFERRED_RE = re.compile(r"^[-*]\s+(Deferred\b.*)", re.IGNORECASE)
DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
DATE_AT_START = re.compile(r"^\s*(?:[-*]\s+)?(?:#+\s*)?\**\s*(\d{4}-\d{2}-\d{2})")
_INLINE_ITEM_RE = re.compile(r'"([^"]+)"|\'([^\']+)\'|([^,\[\]\s]+)')


# ---------------------------------------------------------------- text helpers


def clean(text: str) -> str:
    """Drop a leading byte-order mark and turn Windows line endings into plain ones."""
    if text.startswith("﻿"):
        text = text[1:]
    return text.replace("\r\n", "\n").replace("\r", "\n")


def comment_mask(lines: List[str]) -> List[bool]:
    """True for each line that is part of an HTML `<!-- ... -->` note starting at line start."""
    mask, inside = [], False
    for line in lines:
        stripped = line.strip()
        if inside:
            mask.append(True)
            inside = "-->" not in stripped
        elif stripped.startswith("<!--"):
            mask.append(True)
            inside = "-->" not in stripped[4:]
        else:
            mask.append(False)
    return mask


def heading_name(line: str) -> Optional[str]:
    """'threads / open loops' for '## Threads / Open Loops' (level-2 headings only)."""
    match = re.match(r"^##\s+(.+?)\s*#*\s*$", line)
    if not match or line.startswith("###"):
        return None
    return " ".join(match.group(1).lower().split())


def section_bounds(lines: List[str], heading: str) -> Tuple[Optional[int], int]:
    """(start, end) of a `## ` section: the heading's line index and the next `## ` line.
    Case and spacing in the heading don't matter."""
    want = heading_name(heading)
    start = None
    for index, line in enumerate(lines):
        name = heading_name(line)
        if start is None:
            if name is not None and name == want:
                start = index
        elif name is not None:
            return start, index
    return start, len(lines)


def _sections(lines: List[str]) -> Iterator[Tuple[Optional[str], int, int]]:
    """(name, first line, end) for the preamble (name None) and each `## ` section."""
    name, begin = None, 0
    for index, line in enumerate(lines):
        found = heading_name(line)
        if found is not None:
            yield name, begin, index
            name, begin = found, index + 1
    yield name, begin, len(lines)


# ---------------------------------------------------------------- frontmatter


def parse_inline_list(value: str) -> List[str]:
    """Items of an inline `[a, "b c"]` list; a bare value is not a list."""
    value = value.strip()
    if not value.startswith("["):
        return []
    return [next(part for part in m.groups() if part) for m in _INLINE_ITEM_RE.finditer(value)]


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def frontmatter_end(lines: List[str]) -> Optional[int]:
    """Index of the closing `---` line, or None when there is no complete frontmatter block."""
    if not lines or lines[0].strip() != "---":
        return None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return index
    return None


def parse_frontmatter(text: str, defaults: bool = True) -> Tuple[Dict[str, Any], str]:
    """(meta, body). `links` is a list, `comms` a dict; everything else a string. With
    `defaults=False` a missing `links`/`comms` stays missing. No frontmatter: ({...}, text)."""
    meta: Dict[str, Any] = {"links": [], "comms": {}} if defaults else {}
    text = clean(text)
    lines = text.split("\n")
    end = frontmatter_end(lines)
    if end is None:
        return meta, text
    top = sub = None
    for line in lines[1:end]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()
        if indent == 0 and not stripped.startswith("- "):
            if ":" not in stripped:
                continue
            sub = None
            key, _, value = stripped.partition(":")
            top = key.strip().lower()
            if top == "links":
                meta["links"] = list(meta.get("links", [])) + parse_inline_list(value)
            elif top == "comms":
                meta["comms"] = {}
            else:
                meta[top] = _unquote(value)
        elif stripped.startswith("- "):
            item = _unquote(stripped[2:])
            if top == "links":
                meta.setdefault("links", []).append(item)
            elif top == "comms" and sub and isinstance(meta["comms"].get(sub), list):
                meta["comms"][sub].append(item)
        elif top == "comms" and ":" in stripped:
            key, _, value = stripped.partition(":")
            sub = key.strip().lower()
            value = value.strip()
            if value == "[]" or (not value and sub in COMMS_LIST_KEYS):
                meta["comms"][sub] = []
            else:
                meta["comms"][sub] = _unquote(value)
    return meta, "\n".join(lines[end + 1:])


def task_meta(meta: Dict[str, Any]) -> Dict[str, Any]:
    """A copy with old key names (`created`, `updated`, `due`) also under the new ones."""
    out = dict(meta)
    for old, new in OLD_KEYS.items():
        if new not in out and old in out:
            out[new] = out[old]
    return out


# ---------------------------------------------------------------- threads


def is_new_format(text: str) -> bool:
    lines = clean(text).split("\n")
    return section_bounds(lines, THREADS_HEADING)[0] is not None


def _thread_ranges(lines: List[str]) -> List[Tuple[int, int]]:
    """Where to look for threads: the Threads section; in an old file, its `## Next` section,
    else every part of the body outside the dated history."""
    start, end = section_bounds(lines, THREADS_HEADING)
    if start is not None:
        return [(start + 1, end)]
    body_from = (frontmatter_end(lines) or -1) + 1
    old = [(begin, stop) for name, begin, stop in _sections(lines)
           if name is not None and name.startswith(OLD_NEXT_HEADINGS)]
    if old:
        return old[:1]
    return [(max(begin, body_from), stop) for name, begin, stop in _sections(lines)
            if not (name or "").startswith(OLD_LOG_HEADINGS) and stop > body_from]


def extract_threads(text: str) -> List[Dict[str, Any]]:
    """Tagged top-level bullets (plus their indented continuation lines) as thread dicts:
    index, tag, text, title, kind, why, brief, recommend, preview, snooze_until, park_until,
    defer_hash, start, end, and any of OPTIONAL_FIELDS that are filled in."""
    lines = clean(text).split("\n")
    mask = comment_mask(lines)
    threads: List[Dict[str, Any]] = []
    for first, end in _thread_ranges(lines):
        i = first
        while i < end:
            match = None if mask[i] else TAG_RE.match(lines[i])
            deferred = None if match or mask[i] else DEFERRED_RE.match(lines[i])
            if not (match or deferred):
                i += 1
                continue
            if match:
                tag = match.group(1).lower()
                tag, lead = OLD_TAGS.get(tag, tag), match.group(2)
            else:
                tag, lead = "deferred", deferred.group(1)
            parts, context, current = [lead], {f: [] for f in THREAD_CONTEXT_FIELDS.values()}, ""
            j = i + 1
            while j < end and lines[j].startswith("  ") and lines[j].strip() and not mask[j]:
                stripped = lines[j].strip()
                field = THREAD_CONTEXT_RE.match(stripped)
                if field:
                    current = THREAD_CONTEXT_FIELDS[field.group(1).lower()]
                    context[current].append(field.group(2).strip())
                elif re.match(r"^\s*[-*]\s", lines[j]):
                    current = ""
                elif current:
                    context[current].append(stripped)
                else:
                    parts.append(stripped)
                j += 1
            lead_text = " ".join(parts).strip()
            joined = {dest: " ".join(v for v in vals if v).strip() for dest, vals in context.items()}
            thread: Dict[str, Any] = {
                "index": len(threads), "tag": tag, "text": lead_text,
                "title": joined["title"] or thread_title(lead_text, tag),
                "kind": thread_kind(lead_text, tag), "why": joined["why"], "brief": joined["brief"],
                "recommend": joined["recommend"], "preview": joined["preview"],
                "snooze_until": joined["snooze_until"], "park_until": joined["park_until"],
                "defer_hash": joined["defer_hash"], "start": i, "end": j,
            }
            for dest in OPTIONAL_FIELDS:
                if joined[dest]:
                    thread[dest] = joined[dest]
            threads.append(thread)
            i = j
    return threads


def thread_kind(text: str, tag: str) -> str:
    """question (`[human] Q: …`), approval (`[human] Approve …`), deferred, or thread."""
    if tag == "deferred":
        return "deferred"
    if tag == "human" and re.match(r"^q\s*[:.]", text, re.IGNORECASE):
        return "question"
    if tag == "human" and (re.match(r"^approve\b", text, re.IGNORECASE)
                           or (re.search(r"tasks/_artifacts/", text)
                               and re.search(r"\bdraft\b", text, re.IGNORECASE))):
        return "approval"
    return "thread"


def thread_title(text: str, tag: str) -> str:
    """The card's one-line lead: no `Q:` prefix and no `[options: …]` list."""
    title = text
    if thread_kind(text, tag) == "question":
        title = re.sub(r"^q\s*[:.]\s*", "", title, flags=re.IGNORECASE)
        title = re.sub(r"\s*\[options?:[^\]]*\]\s*", " ", title, flags=re.IGNORECASE)
    return " ".join(title.split()).strip()


# ---------------------------------------------------------------- Outcome, Pinned, Activity


def _top_bullets(lines: List[str], mask: List[bool], first: int, end: int) -> List[str]:
    out, i = [], first
    while i < end:
        if not mask[i] and re.match(r"^[-*]\s+\S", lines[i]):
            block, j = [lines[i].strip()[2:].strip()], i + 1
            while j < end and lines[j].startswith((" ", "\t")) and lines[j].strip() and not mask[j]:
                block.append(lines[j].strip())
                j += 1
            out.append(" ".join(block))
            i = j
        else:
            i += 1
    return out


def outcome(text: str) -> str:
    """The Outcome section as one line (notes left out)."""
    lines = clean(text).split("\n")
    start, end = section_bounds(lines, OUTCOME_HEADING)
    if start is None:
        return ""
    mask = comment_mask(lines)
    return " ".join(lines[i].strip() for i in range(start + 1, end) if lines[i].strip() and not mask[i])


def pinned(text: str) -> List[str]:
    """Each settled fact under `## Pinned` (continuation lines joined)."""
    lines = clean(text).split("\n")
    start, end = section_bounds(lines, PINNED_HEADING)
    return [] if start is None else _top_bullets(lines, comment_mask(lines), start + 1, end)


def activity_entries(text: str) -> List[Dict[str, Any]]:
    """Activity entries in file order (newest first by the rules): {date, header, lines}.
    New format: one entry per `### YYYY-MM-DD HH:MM UTC` heading. Old format (a Log, or an
    Activity with no `###` headings): one entry per line that starts with a date."""
    lines = clean(text).split("\n")
    mask = comment_mask(lines)
    span = next(((b, e) for name, b, e in _sections(lines)
                 if name is not None and name.startswith(OLD_LOG_HEADINGS)), None)
    if span is None:
        return []
    entries: List[Dict[str, Any]] = []
    headed = any(lines[i].startswith("###") and not mask[i] for i in range(*span))
    current: Optional[Dict[str, Any]] = None
    for i in range(*span):
        line = lines[i]
        if mask[i]:
            continue
        if headed and line.startswith("###"):
            found = DATE_RE.search(line)
            current = {"date": found.group(1) if found else "", "header": line.lstrip("#").strip(),
                       "lines": []}
            entries.append(current)
        elif not headed and not line[:1].isspace() and DATE_AT_START.match(line):
            current = {"date": DATE_AT_START.match(line).group(1), "header": "", "lines": [line]}
            entries.append(current)
        elif current is not None and line.strip():
            current["lines"].append(line)
    return entries


def last_activity_date(text: str) -> Optional[str]:
    """Newest date in Activity (or an old Log); with neither, any line-leading date in the body."""
    found = [e["date"] for e in activity_entries(text) if e["date"]]
    if not found:
        _, body = parse_frontmatter(text)
        lines = body.split("\n")
        mask = comment_mask(lines)
        found = [m.group(1) for i, m in enumerate(DATE_AT_START.match(line) for line in lines)
                 if m and not mask[i]]
    return max(found) if found else None


def read_task(text: str) -> Dict[str, Any]:
    """Everything the brief and reviews need from one file, old or new format."""
    text = clean(text)
    meta, body = parse_frontmatter(text)
    heading = next((line[2:].strip() for line in body.split("\n") if line.startswith("# ")), "")
    return {"meta": task_meta(meta), "heading": heading,
            "format": "new" if is_new_format(text) else "old", "outcome": outcome(text),
            "pinned": pinned(text), "threads": extract_threads(text),
            "last_activity": last_activity_date(text)}
