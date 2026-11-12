"""jobs/weekly_review.py: the week's packet, the engine-written review, the fallback, no Gmail."""

from __future__ import annotations

import json
import logging
import os
import re

import pytest

from brainkit import engine
from brainkit.config import Config
from brainkit.jobs import weekly_review
from brainkit.jobs.base import JobContext
from brainkit.runner import FakeRunner, Result
from brainkit.runs import RunsLedger


class Week:
    def __init__(self, ws, at, tmp_path):
        self.ws = ws
        self.now = at(2026, 10, 26, 7, 30)    # Monday
        self.since = at(2026, 10, 19, 7, 31)  # last Monday's review
        self.claude = tmp_path / "bin" / "claude"
        self.claude.parent.mkdir()
        self.claude.write_text("#!/bin/sh\n")
        self.claude.chmod(0o755)
        self.mode = "write"
        self.prompts = []
        self.runner = FakeRunner()
        self.runner.on("security", "find-generic-password", "-s", "brain.claude-oauth-token", result=Result(44))
        self.runner.on(str(self.claude), result=self.engine)
        self.log = logging.getLogger(f"test-weekly-{ws.root}")
        self.log.handlers = [logging.FileHandler(str(ws.logs / "brain.log"), encoding="utf-8")]
        self.log.setLevel(logging.INFO)
        self.log.propagate = False
        for name in ("2026-10-12.md", "2026-10-19.md", "2026-10-20.md", "2026-10-20-2.md", "2026-10-23.md",
                     "weekly-2026-W42.md"):
            (ws.briefs / name).write_text("x\n")
        (ws.root / "strategy.md").write_text("# Strategy\n")
        active = ws.tasks / "active"
        active.mkdir(parents=True)
        (active / "hire.md").write_text("---\ntitle: Hire a bookkeeper\ndue: 2026-11-15\n---\n- [me] Shortlist two\n")
        archive = ws.tasks / "archive"
        archive.mkdir(parents=True)
        done = archive / "website.md"
        done.write_text("# New website live\n")
        inside = at(2026, 10, 22, 15).timestamp()
        os.utime(str(done), (inside, inside))
        old = archive / "old.md"
        old.write_text("# Long done\n")
        stamp = at(2026, 10, 1).timestamp()
        os.utime(str(old), (stamp, stamp))
        runs = RunsLedger(ws)
        runs.record("slack_sync", "error", at(2026, 10, 15, 9), at(2026, 10, 15, 9, 1), detail="before the window")
        runs.record("slack_sync", "error", at(2026, 10, 21, 9), at(2026, 10, 21, 9, 1), detail="ratelimited")
        runs.record("slack_sync", "error", at(2026, 10, 22, 9), at(2026, 10, 22, 9, 1), detail="ratelimited again")
        runs.record("slack_sync", "ok", at(2026, 10, 22, 10), at(2026, 10, 22, 10, 1))

    def engine(self, argv):
        self.prompts.append(argv[2])
        if self.mode == "fail":
            return Result(1, "", "boom")
        target = re.search(r"Write the review to: (\S+)", argv[2]).group(1)
        (self.ws.root / target).write_text("# Your week\n\nGood progress.\n")
        return Result(0, json.dumps({"type": "result", "subtype": "success", "is_error": False,
                                     "result": "Done.", "total_cost_usd": 0.2}))

    def ctx(self):
        env = {"PATH": str(self.claude.parent), "HOME": str(self.ws.root.parent), "USER": "tester"}
        return JobContext(ws=self.ws, config=Config(), runner=self.runner, now=self.now, runs=RunsLedger(self.ws),
                          log=self.log, notify=lambda title, message: None, env=env, since=self.since)


@pytest.fixture
def week(ws, at, tmp_path):
    return Week(ws, at, tmp_path)


def test_week_label_names_the_reviewed_week(at):
    assert weekly_review.week_label(at(2026, 10, 19, 7, 30), at(2026, 10, 26, 7, 30), "Europe/London") == "2026-W43"
    assert weekly_review.week_label(at(2026, 12, 28, 7, 30), at(2027, 1, 4, 7, 30), "Europe/London") == "2026-W53"


def test_review_is_written_from_the_weeks_packet(week):
    result = weekly_review.run(week.ctx())
    assert (result.status, result.summary) == ("ok", "weekly review ready")
    assert result.data == {"path": "briefs/weekly-2026-W43.md", "fallback": False, "cost_usd": 0.2,
                           "window_start": "2026-10-19T07:31:00+01:00", "window_end": "2026-10-26T07:30:00+00:00",
                           "complete": True}
    assert week.prompts == ["/weekly-review\n\nData packet: briefs/.packets/weekly-2026-W43.json\n"
                            "Write the review to: briefs/weekly-2026-W43.md\n"
                            "It covers Mon 19 Oct 07:31 to Mon 26 Oct 07:30."]
    packet = json.loads((week.ws.packets / "weekly-2026-W43.json").read_text())
    assert packet["briefs"] == ["briefs/2026-10-19.md", "briefs/2026-10-20-2.md", "briefs/2026-10-20.md",
                                "briefs/2026-10-23.md"]
    assert packet["tasks"][0]["title"] == "Hire a bookkeeper"
    assert packet["archived_this_week"] == [{"path": "tasks/archive/website.md", "title": "New website live"}]
    assert [(e["job"], e["count"], e["detail"]) for e in packet["job_errors"]] == [("slack_sync", 2, "ratelimited again")]
    assert packet["files"] == {"strategy": "strategy.md", "previous_review": "briefs/weekly-2026-W42.md"}
    text = (week.ws.briefs / "weekly-2026-W43.md").read_text()
    assert text.startswith("> Generated Mon 26 Oct 07:30 · covers since Mon 19 Oct 07:31\n\n# Your week")
    assert not [argv for argv in week.runner.argvs() if argv[0] == "gws"]  # no Gmail draft, no mail reads


def test_engine_failure_gives_a_short_plain_review(week):
    week.mode = "fail"
    result = weekly_review.run(week.ctx())
    assert result.summary == "fallback weekly review (AI unavailable)" and result.data["fallback"]
    assert result.data["complete"] is False  # next week's review covers this week again
    lines = (week.ws.briefs / "weekly-2026-W43.md").read_text().splitlines()
    assert lines[0].startswith("> Generated Mon 26 Oct 07:30")
    assert lines[2].startswith("AI summary unavailable this week: Claude gave no readable answer")
    assert "- briefs/2026-10-23.md" in lines and "- New website live (tasks/archive/website.md)" in lines
    assert "- Hire a bookkeeper (due 2026-11-15) — next: [human] Shortlist two" in lines
    assert "- slack_sync failed 2 time(s): ratelimited again" in lines
    log_text = (week.ws.logs / "brain.log").read_text()
    assert "Hire a bookkeeper" not in log_text and "New website" not in log_text


def test_an_existing_review_is_kept(week):
    existing = week.ws.briefs / "weekly-2026-W43.md"
    existing.write_text("written by hand\n")
    result = weekly_review.run(week.ctx())
    assert result.data["path"] == "briefs/weekly-2026-W43-2.md" and existing.read_text() == "written by hand\n"
    packet = json.loads((week.ws.packets / "weekly-2026-W43-2.json").read_text())
    assert packet["files"]["previous_review"] == "briefs/weekly-2026-W43.md"


def test_engine_uses_the_default_tools(week):
    weekly_review.run(week.ctx())
    argv = [a for a in week.runner.argvs() if a[0] == str(week.claude)][0]
    assert argv[argv.index("--allowedTools") + 1:] == engine.DEFAULT_TOOLS


def test_review_reads_new_format_tasks(week, at):
    (week.ws.tasks / "active" / "hire.md").write_text(
        '---\ntitle: "Hire a bookkeeper"\npriority: "P2"\ncreated_at: "2026-10-01"\nupdated_at: "2026-10-20"\n'
        'due_at: "2026-11-15"\ncompleted_at: ""\nlinks: []\n---\n\n## Outcome\n\nA bookkeeper starts.\n\n'
        "## Pinned\n\n## Threads / Open Loops\n\n- [human] Approve the job advert?\n  Context: Two lines long.\n\n"
        "## Activity\n\n### 2026-10-20 10:00 UTC\n- Advert drafted.\n")
    done = week.ws.tasks / "archive" / "website.md"
    done.write_text('---\ntitle: "New website live"\ncompleted_at: "2026-10-22"\n---\n\n## Outcome\n\nLive.\n')
    inside = at(2026, 10, 22, 15).timestamp()
    os.utime(str(done), (inside, inside))
    week.mode = "fail"
    weekly_review.run(week.ctx())
    packet = json.loads((week.ws.packets / "weekly-2026-W43.json").read_text())
    [task] = packet["tasks"]
    assert task["threads"] == [{"tag": "human", "kind": "approval", "text": "Approve the job advert?",
                                "context": "Two lines long."}]
    assert task["last_log"] == "2026-10-20" and task["due"] == "2026-11-15"
    assert packet["archived_this_week"] == [{"path": "tasks/archive/website.md", "title": "New website live"}]
    lines = (week.ws.briefs / "weekly-2026-W43.md").read_text().splitlines()
    assert "- Hire a bookkeeper (due 2026-11-15) — next: [human] Approve the job advert?" in lines
