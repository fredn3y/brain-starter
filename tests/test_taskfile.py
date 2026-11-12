"""taskfile.py: one reader for task files, in the shared format and the kit's old one."""

from __future__ import annotations

from pathlib import Path

from brainkit import taskfile

REPO = Path(__file__).resolve().parents[1]

NEW = """---
title: "Open the second shop"
project: "Shop"
priority: "P2"
created_at: "2026-10-01"
updated_at: "2026-10-20"
due_at: ""
blocker: "Waits on the lease"
completed_at: ""
links: ["https://example.com/plan", tasks/active/notes.md]
comms:
  gmail_labels: []
  gmail_query: "from:landlord@example.com"
  slack_channels:
    - shop-two
  slack_search: ""
---

## Outcome

The second shop is open.
Done when the first sale is made.

## Pinned

- The landlord wants rent up front. (2026-10-05)
- Opening hours are 9 to 5,
  Monday to Saturday. (2026-10-06)

## Threads / Open Loops

- [ai] Draft the opening-day email
- [ai] From 2026-11-01: Start the stock count
- [human] Q: Saturday or Monday? [options: Saturday | Monday]
  Context: Saturdays are busier,
  and the fitters finish on Friday.
  Recommend: Saturday
  Snooze-Until: 2026-10-25
- [human] Approve sending the landlord the signed form?
  To: the landlord
  Channel: Email
  Draft: artifact:tasks/_artifacts/shop/form-reply.md
- [external] Signed lease
  Follow-Up: 2026-10-28
- Deferred (on the lease being signed; review by 2026-11-15): order the sign
- An untagged note is not a thread

## Activity

### 2026-10-20 14:05 UTC
- Fitters booked.
- [ai] An old line in history is not a thread.

### 2026-10-12 09:00 UTC
- Viewed the unit.
"""

OLD = """---
title: Launch the spring range
priority: P1
created: 2026-10-01
updated: '2026-10-20'
due: 2026-11-01
---

## Outcome

The range is on sale.

## Next

<!--
  - [me] example inside a note
- [me] even at the start of a line inside a note
-->
- [ai] Draft the supplier email
- [ME] Approve the price list
* [waiting] Samples from the factory

## Log

- 2026-10-20: Prices drafted.
- 2026-10-18: Kicked off.
"""


def test_frontmatter_matches_the_shared_shape():
    meta, body = taskfile.parse_frontmatter(NEW)
    assert meta["title"] == "Open the second shop" and meta["project"] == "Shop"
    assert meta["blocker"] == "Waits on the lease" and meta["due_at"] == ""
    assert meta["links"] == ["https://example.com/plan", "tasks/active/notes.md"]
    assert meta["comms"] == {"gmail_labels": [], "gmail_query": "from:landlord@example.com",
                             "slack_channels": ["shop-two"], "slack_search": ""}
    assert body.startswith("\n## Outcome")


def test_frontmatter_defaults_and_missing_blocks():
    assert taskfile.parse_frontmatter("no frontmatter\n") == ({"links": [], "comms": {}}, "no frontmatter\n")
    assert taskfile.parse_frontmatter("---\ntitle: x\n", defaults=False) == ({}, "---\ntitle: x\n")
    meta, _ = taskfile.parse_frontmatter("---\ntitle: x\n---\n", defaults=False)
    assert meta == {"title": "x"}


def test_old_keys_are_read_under_the_new_names():
    meta = taskfile.task_meta(taskfile.parse_frontmatter(OLD)[0])
    assert (meta["created_at"], meta["updated_at"], meta["due_at"]) == ("2026-10-01", "2026-10-20", "2026-11-01")
    both = taskfile.task_meta({"due": "2026-01-01", "due_at": "2026-02-02"})
    assert both["due_at"] == "2026-02-02"


def test_threads_match_the_shared_shape():
    threads = taskfile.extract_threads(NEW)
    assert [(t["index"], t["tag"], t["kind"]) for t in threads] == [
        (0, "ai", "thread"), (1, "ai", "thread"), (2, "human", "question"), (3, "human", "approval"),
        (4, "external", "thread"), (5, "deferred", "deferred")]
    question = threads[2]
    assert question["text"] == "Q: Saturday or Monday? [options: Saturday | Monday]"
    assert question["title"] == "Saturday or Monday?"
    assert question["context"] == "Saturdays are busier, and the fitters finish on Friday."
    assert question["recommend"] == "Saturday" and question["snooze_until"] == "2026-10-25"
    assert set(question) == {"index", "tag", "text", "title", "kind", "why", "brief", "recommend", "preview",
                             "snooze_until", "park_until", "defer_hash", "start", "end", "context"}
    approval = threads[3]
    assert (approval["to"], approval["channel"], approval["draft"]) == (
        "the landlord", "Email", "artifact:tasks/_artifacts/shop/form-reply.md")
    assert threads[4]["follow_up"] == "2026-10-28" and "follow_up" not in threads[0]
    assert threads[5]["text"] == "Deferred (on the lease being signed; review by 2026-11-15): order the sign"
    lines = NEW.split("\n")
    assert lines[question["start"]].startswith("- [human] Q:") and lines[question["end"]].startswith("- [human] Approve")


def test_old_format_threads_get_the_new_tags_and_notes_are_skipped():
    threads = taskfile.extract_threads(OLD)
    assert [(t["tag"], t["text"]) for t in threads] == [
        ("ai", "Draft the supplier email"), ("human", "Approve the price list"),
        ("external", "Samples from the factory")]


def test_old_file_without_sections_reads_tags_anywhere_but_the_log():
    text = "# Office move\n\n- [me] Book the van\n\n## Log\n- 2026-10-01: [me] said yes\n"
    assert [(t["tag"], t["text"]) for t in taskfile.extract_threads(text)] == [("human", "Book the van")]


def test_windows_line_endings_and_byte_order_mark():
    text = "﻿" + NEW.replace("\n", "\r\n")
    assert taskfile.parse_frontmatter(text)[0]["title"] == "Open the second shop"
    assert len(taskfile.extract_threads(text)) == 6
    assert taskfile.read_task(text)["last_activity"] == "2026-10-20"


def test_outcome_pinned_and_activity():
    assert taskfile.outcome(NEW) == "The second shop is open. Done when the first sale is made."
    assert taskfile.pinned(NEW) == ["The landlord wants rent up front. (2026-10-05)",
                                    "Opening hours are 9 to 5, Monday to Saturday. (2026-10-06)"]
    entries = taskfile.activity_entries(NEW)
    assert [(e["date"], e["header"]) for e in entries] == [("2026-10-20", "2026-10-20 14:05 UTC"),
                                                         ("2026-10-12", "2026-10-12 09:00 UTC")]
    assert entries[0]["lines"] == ["- Fitters booked.", "- [ai] An old line in history is not a thread."]


def test_old_log_lines_are_entries():
    entries = taskfile.activity_entries(OLD)
    assert [(e["date"], e["lines"]) for e in entries] == [("2026-10-20", ["- 2026-10-20: Prices drafted."]),
                                                         ("2026-10-18", ["- 2026-10-18: Kicked off."])]
    assert taskfile.last_activity_date(OLD) == "2026-10-20"
    assert taskfile.last_activity_date("---\ntitle: x\n---\n2026-03-01 a note\n") == "2026-03-01"
    assert taskfile.last_activity_date("nothing dated\n") is None


def test_read_task_on_both_formats():
    new, old = taskfile.read_task(NEW), taskfile.read_task(OLD)
    assert new["format"] == "new" and old["format"] == "old"
    assert old["meta"]["due_at"] == "2026-11-01" and old["outcome"] == "The range is on sale."
    assert new["pinned"][0].startswith("The landlord") and old["pinned"] == []


def test_the_template_is_a_new_format_file_with_no_live_threads():
    text = (REPO / "kit" / "tasks" / "TEMPLATE.md").read_text(encoding="utf-8")
    meta, _ = taskfile.parse_frontmatter(text, defaults=False)
    assert list(meta) == ["title", "priority", "created_at", "updated_at", "due_at", "completed_at", "links"]
    assert taskfile.is_new_format(text) and taskfile.extract_threads(text) == []
    assert taskfile.pinned(text) == [] and taskfile.outcome(text) == ""
    headings = [line for line in text.splitlines() if line.startswith("## ")]
    assert headings == ["## Outcome", "## Pinned", "## Threads / Open Loops", "## Activity"]
    # Examples inside the notes never start at column 0, so a stricter reader skips them too.
    assert not [line for line in text.splitlines() if line.startswith(("- [", "- Deferred"))]
