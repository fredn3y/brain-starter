"""migrations/0002_task_format.py: old task files move to the shared format without losing a line."""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

from brainkit import taskfile

REPO = Path(__file__).resolve().parents[1]


def load():
    spec = importlib.util.spec_from_file_location("migration_0002", str(REPO / "migrations" / "0002_task_format.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules["migration_0002"] = module
    spec.loader.exec_module(module)
    return module


MIG = load()

OLD_TIDY = """---
title: Launch the spring range
priority: P1
created: 2026-10-01
updated: 2026-10-20
due: 2026-11-01
---

<!--
How to use: copy to tasks/active/<short-name>.md, fill it in, then delete these notes.
-->

## Outcome

<!-- One sentence: what "done" looks like. -->
The spring range is on sale in the shop.

## Next

<!--
  - [ai] something the assistant can do or draft
  - [me] something only you can do or decide
-->
- [ai] Draft the supplier email
- [me] Approve the price list
- [waiting] Samples from the factory (since 2026-10-02)

## Log

<!-- Dated lines, newest first. Append only. -->

- 2026-10-20: Prices drafted.
  The factory price went up 3%.
- 2026-10-20: Asked for samples.
- 2026-10-18: Kicked off. [me] said go.
"""

NEW_TIDY = """---
title: Launch the spring range
priority: P1
created_at: 2026-10-01
updated_at: 2026-10-20
due_at: 2026-11-01
completed_at: ""
links: []
---

<!--
How to use: copy to tasks/active/<short-name>.md, fill it in, then delete these notes.
-->

## Outcome

<!-- One sentence: what "done" looks like. -->
The spring range is on sale in the shop.

## Pinned

## Threads / Open Loops

<!--
  - [ai] something the assistant can do or draft
  - [me] something only you can do or decide
-->
- [ai] Draft the supplier email
- [human] Approve the price list
- [external] Samples from the factory (since 2026-10-02)

## Activity

<!-- Dated lines, newest first. Append only. -->

### 2026-10-20
- 2026-10-20: Prices drafted.
  The factory price went up 3%.
- 2026-10-20: Asked for samples.

### 2026-10-18
- 2026-10-18: Kicked off. [me] said go.
"""


def put(ws, rel, text, mtime=None):
    path = ws.root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8") if isinstance(text, str) else text)
    if mtime:
        os.utime(str(path), (mtime, mtime))
    return path


def lines_kept(old: str, new: str, changed=()) -> None:
    """Every old line is still there, in order (apart from the ones the migration retags)."""
    remaining = iter(new.splitlines())
    for line in old.splitlines():
        if line in changed:
            continue
        assert any(line == candidate for candidate in remaining), f"lost or moved: {line!r}"


# ---------------------------------------------------------------- tidy files


def test_tidy_old_file_becomes_the_new_format(ws):
    path = put(ws, "tasks/active/launch.md", OLD_TIDY)
    message = MIG.migrate(ws.root)
    assert path.read_text() == NEW_TIDY
    assert message == ("task files: moved 1 task file(s) to the new format "
                       "(originals copied to .kit/state/migrations/0002/)")
    task = taskfile.read_task(path.read_text())
    assert task["format"] == "new" and task["meta"]["due_at"] == "2026-11-01"
    assert [(t["tag"], t["text"]) for t in task["threads"]] == [
        ("ai", "Draft the supplier email"), ("human", "Approve the price list"),
        ("external", "Samples from the factory (since 2026-10-02)")]
    assert [e["date"] for e in taskfile.activity_entries(path.read_text())] == ["2026-10-20", "2026-10-18"]


def test_every_old_line_survives_in_order(ws):
    path = put(ws, "tasks/active/launch.md", OLD_TIDY)
    MIG.migrate(ws.root)
    retagged = {"- [me] Approve the price list", "- [waiting] Samples from the factory (since 2026-10-02)",
                "created: 2026-10-01", "updated: 2026-10-20", "due: 2026-11-01", "## Next", "## Log"}
    lines_kept(OLD_TIDY, path.read_text(), retagged)


def test_running_twice_changes_nothing_more(ws):
    path = put(ws, "tasks/active/launch.md", OLD_TIDY)
    MIG.migrate(ws.root)
    first = path.read_bytes()
    assert MIG.migrate(ws.root) == "task files: nothing to do"
    assert path.read_bytes() == first
    assert len(list((ws.root / MIG.BACKUP_DIR).rglob("*.md"))) == 1


def test_already_new_file_is_left_alone(ws):
    new = (REPO / "kit" / "tasks" / "TEMPLATE.md").read_text()
    path = put(ws, "tasks/active/fresh.md", NEW_TIDY)
    other = put(ws, "tasks/later/from-template.md", new)
    assert MIG.migrate(ws.root) == "task files: nothing to do"
    assert path.read_text() == NEW_TIDY and other.read_text() == new
    assert not (ws.root / MIG.BACKUP_DIR).exists()


def test_mixed_folder_reports_each_kind(ws):
    put(ws, "tasks/active/launch.md", OLD_TIDY)
    put(ws, "tasks/active/fresh.md", NEW_TIDY)
    message = MIG.migrate(ws.root)
    assert "moved 1 task file(s)" in message and "1 already in the new format" in message


# ---------------------------------------------------------------- backups and file details


def test_original_is_backed_up_first_with_its_times(ws, at):
    stamp = at(2026, 10, 20, 9).timestamp()
    path = put(ws, "tasks/archive/2026/site.md", OLD_TIDY, mtime=stamp)
    path.chmod(0o600)
    MIG.migrate(ws.root)
    backup = ws.root / ".kit/state/migrations/0002/tasks/archive/2026/site.md"
    assert backup.read_text() == OLD_TIDY
    assert int(backup.stat().st_mtime) == int(stamp)
    # The task keeps its modified time (the weekly review reads it) and its permissions.
    assert int(path.stat().st_mtime) == int(stamp) and (path.stat().st_mode & 0o777) == 0o600
    assert oct((ws.root / MIG.BACKUP_DIR).stat().st_mode & 0o777) == "0o700"


def test_a_different_earlier_backup_is_never_overwritten(ws):
    put(ws, ".kit/state/migrations/0002/tasks/active/launch.md", "an older copy\n")
    put(ws, "tasks/active/launch.md", OLD_TIDY)
    MIG.migrate(ws.root)
    folder = ws.root / ".kit/state/migrations/0002/tasks/active"
    assert (folder / "launch.md").read_text() == "an older copy\n"
    assert (folder / "launch-2.md").read_text() == OLD_TIDY


def test_archived_file_gets_completed_at_from_its_updated_date(ws):
    path = put(ws, "tasks/archive/site.md", "---\ntitle: Old site\nupdated: '2026-09-01'\n---\n"
                                            "## Outcome\n\nLive.\n\n## Log\n\n- 2026-09-01: Done.\n")
    MIG.migrate(ws.root)
    meta, _ = taskfile.parse_frontmatter(path.read_text(), defaults=False)
    assert meta["completed_at"] == "2026-09-01" and meta["updated_at"] == "2026-09-01"
    assert meta["links"] == []
    active = put(ws, "tasks/active/x.md", "---\ntitle: X\nupdated: 2026-09-01\n---\n## Next\n- [ai] Go\n")
    MIG.migrate(ws.root)
    assert taskfile.parse_frontmatter(active.read_text())[0]["completed_at"] == ""


def test_windows_line_endings_and_byte_order_mark_are_kept(ws):
    raw = ("﻿" + OLD_TIDY).replace("\n", "\r\n").encode("utf-8")
    path = put(ws, "tasks/active/launch.md", raw)
    MIG.migrate(ws.root)
    data = path.read_bytes()
    assert data.startswith("﻿".encode("utf-8"))
    assert data.decode("utf-8")[1:].replace("\r\n", "\n") == NEW_TIDY
    assert b"\n" not in data.replace(b"\r\n", b"")


# ---------------------------------------------------------------- awkward hand-edited files


def test_hand_edited_file_without_sections(ws):
    old = "---\ntitle: Office move\ndue:\n---\n# Office move\n\n- [ME] Book the van\n* [waiting] Keys from the landlord\n"
    path = put(ws, "tasks/active/move.md", old)
    MIG.migrate(ws.root)
    text = path.read_text()
    assert text == ("---\ntitle: Office move\ndue_at:\ncompleted_at: \"\"\nlinks: []\n---\n# Office move\n\n"
                    "## Outcome\n\n## Pinned\n\n## Threads / Open Loops\n\n"
                    "- [human] Book the van\n- [external] Keys from the landlord\n\n## Activity\n")
    assert [t["tag"] for t in taskfile.extract_threads(text)] == ["human", "external"]


def test_extra_sections_odd_headings_and_no_trailing_newline(ws):
    old = ("---\nTitle: \"Hire a bookkeeper\"\npriority: P2\ncreated: 2026-10-01\ncreated_at: 2026-10-02\n"
           "tags: money\n---\n\n## Outcome\nA bookkeeper starts.\n\n## Notes\n- Ask a friend first.\n\n"
           "## Next steps\n- [ai] Draft the advert\n  - [me] sub-step stays indented\n- [x] Done thing\n\n"
           "## History\n2026-10-03 Talked to the bank\n```\n2026-01-01 code, not history\n```\n"
           "- 2026-10-01: Started")
    path = put(ws, "tasks/active/hire.md", old)
    MIG.migrate(ws.root)
    text = path.read_text()
    # created_at already there: the old key stays too (nothing is lost) and is not renamed.
    assert "created: 2026-10-01\ncreated_at: 2026-10-02\n" in text and "updated_at" not in text
    assert "## Outcome\nA bookkeeper starts.\n\n## Pinned\n\n## Notes\n- Ask a friend first.\n" in text
    assert "## Threads / Open Loops\n- [ai] Draft the advert\n  - [human] sub-step stays indented\n- [x] Done thing" in text
    assert text.endswith("## Activity\n\n### 2026-10-03\n2026-10-03 Talked to the bank\n```\n"
                         "2026-01-01 code, not history\n```\n\n### 2026-10-01\n- 2026-10-01: Started")
    lines_kept(old, text, {"## Next steps", "## History", "  - [me] sub-step stays indented"})
    assert MIG.migrate(ws.root) == "task files: nothing to do"


def test_half_migrated_file_finishes_the_job(ws):
    old = ("---\ntitle: X\ncreated_at: 2026-10-01\nupdated: 2026-10-05\n---\n\n## Outcome\n\nDone.\n\n"
           "## Threads / Open Loops\n\n- [me] Call the bank\n\n## Activity\n\n### 2026-10-05 09:00 UTC\n- Moved.\n")
    path = put(ws, "tasks/active/x.md", old)
    MIG.migrate(ws.root)
    text = path.read_text()
    assert "updated_at: 2026-10-05\ncompleted_at: \"\"\nlinks: []\n" in text
    assert "- [human] Call the bank" in text and "## Pinned" in text
    assert text.endswith("### 2026-10-05 09:00 UTC\n- Moved.\n")  # existing entries untouched


def test_log_lines_in_a_new_activity_section_without_headings(ws):
    old = ("---\ntitle: X\n---\n## Outcome\n\n## Threads / Open Loops\n\n## Activity\n\n- 2026-10-02: b\n"
           "- 2026-10-01: a\n")
    path = put(ws, "tasks/active/x.md", old)
    MIG.migrate(ws.root)
    assert path.read_text().endswith("## Activity\n\n### 2026-10-02\n- 2026-10-02: b\n\n### 2026-10-01\n- 2026-10-01: a\n")


# ---------------------------------------------------------------- files it cannot read


@pytest.mark.parametrize("content", [
    "Just some notes, no frontmatter.\n- [me] still a note\n",
    "---\ntitle: Never closed\n\n## Next\n- [me] x\n",
    "---\ntitle: X\nthis line is not a key\n---\n## Next\n- [me] x\n",
    "---\ntitle: Shopping\n---\nMilk, eggs, bread.\n",
    b"---\ntitle: caf\xe9\n---\n## Next\n- [me] x\n",
], ids=["no-frontmatter", "unclosed", "bad-key", "not-a-task", "not-utf8"])
def test_unreadable_file_is_untouched_and_named(ws, content):
    path = put(ws, "tasks/later/odd.md", content)
    before = path.read_bytes()
    put(ws, "tasks/active/launch.md", OLD_TIDY)
    message = MIG.migrate(ws.root)
    assert path.read_bytes() == before
    assert message.endswith("left as they were, not readable as task files: tasks/later/odd.md")
    assert "moved 1 task file(s)" in message
    assert not (ws.root / ".kit/state/migrations/0002/tasks/later/odd.md").exists()


def test_links_hidden_files_and_template_are_skipped(ws, tmp_path):
    outside = tmp_path / "outside.md"
    outside.write_text(OLD_TIDY)
    (ws.tasks / "active").mkdir(parents=True, exist_ok=True)
    (ws.tasks / "active" / "linked.md").symlink_to(outside)
    put(ws, "tasks/active/.draft.md", OLD_TIDY)
    put(ws, "tasks/active/TEMPLATE.md", OLD_TIDY)
    message = MIG.migrate(ws.root)
    assert outside.read_text() == OLD_TIDY
    assert (ws.root / "tasks/active/.draft.md").read_text() == OLD_TIDY
    assert (ws.root / "tasks/active/TEMPLATE.md").read_text() == OLD_TIDY
    assert message == "task files: left as they were, not readable as task files: tasks/active/linked.md"


def test_empty_brain_has_nothing_to_do(ws):
    assert MIG.migrate(ws.root) == "task files: nothing to do"


def test_runs_through_apply_like_any_migration(tmp_path, ws):
    """The real migration file loads the way apply.py loads migrations."""
    from brainkit import apply as apply_mod
    func = apply_mod._load_migration("0002_task_format", REPO / "migrations" / "0002_task_format.py")
    put(ws, "tasks/active/launch.md", OLD_TIDY)
    assert "moved 1" in func(ws.root)
