"""brief.py: packet assembly, per-source isolation, engine and fallback briefs, the header stamp,
the Gmail draft, never overwriting, and keeping personal content out of brain.log."""

from __future__ import annotations

import base64
import email
import json
import logging
import re
from dataclasses import replace
from datetime import timedelta
from email import policy as email_policy
from pathlib import Path

import pytest

from brainkit import brief, engine, gws, schedule
from brainkit.config import Config
from brainkit.jobs import brief as brief_job
from brainkit.jobs.base import JobContext, JobResult
from brainkit.runner import FakeRunner, Result
from brainkit.runs import RunsLedger

NOISE = "Using keyring backend: keyring\n"
TODAY = {"count": 2, "events": [
    {"calendar": "Work", "start": "2026-10-21T10:00:00+01:00", "end": "2026-10-21T10:30:00+01:00",
     "location": "Room 2", "summary": "Supplier call"},
    {"calendar": "Work", "start": "2026-10-21", "end": "2026-10-22", "location": "", "summary": "Office closed"}]}
TOMORROW = {"count": 1, "events": [{"calendar": "Work", "start": "2026-10-22T09:00:00+01:00",
                                    "end": "2026-10-22T09:15:00+01:00", "location": "", "summary": "Standup"}]}
TRIAGE = {"messages": [{"id": f"m{i}", "from": f"Sender {i} <s{i}@example.com>", "subject": f"Invoice {i} overdue",
                        "date": f"Tue, 20 Oct 2026 {8 + i:02d}:00:00 +0100"} for i in range(1, 13)],
          "query": "x", "resultSizeEstimate": 12}
STATUS = {"user": "me@example.com", "token_valid": True, "has_refresh_token": True,
          "scopes": list(gws.REQUIRED_SCOPES)}
AI_BRIEF = "# Your morning\n\nThree things need you today.\n"
PRIVATE = ("Invoice", "s3@example.com", "Can you sign", "Launch the spring", "Body of", "me@example.com",
           "Supplier call", "Shipping update")


def read_mail(argv):
    mid = argv[argv.index("--id") + 1]
    return Result(0, json.dumps({"thread_id": f"t-{mid}", "message_id": f"<{mid}@mail>",
                                 "from": {"name": "Pat Doe", "email": "pat@example.com"},
                                 "to": [{"name": "", "email": "me@example.com"}], "cc": [],
                                 "subject": f"Subject {mid}", "date": "Tue, 20 Oct 2026 10:00:00 +0100",
                                 "body_text": f"Body of {mid}\n\n\n\n" + "x" * 3000, "body_html": ""}), NOISE)


class Scene:
    """A workspace with mail, calendar, Slack, tasks and run history, and a fake engine."""

    def __init__(self, ws, at, tmp_path):
        self.ws = ws
        self.now = at(2026, 10, 21, 9, 12)
        self.since = at(2026, 10, 20, 7, 30)
        self.claude = tmp_path / "bin" / "claude"
        self.claude.parent.mkdir()
        self.claude.write_text("#!/bin/sh\n")
        self.claude.chmod(0o755)
        self.engine_mode = "write"
        self.prompts = []
        self.runner = FakeRunner()
        on = self.runner.on
        on("security", "find-generic-password", "-s", "brain.claude-oauth-token", result=Result(44))
        on("gws", "calendar", "+agenda", "--today", result=Result(0, json.dumps(TODAY), NOISE))
        on("gws", "calendar", "+agenda", "--tomorrow", result=Result(0, json.dumps(TOMORROW), NOISE))
        on("gws", "gmail", "+triage", result=Result(0, json.dumps(TRIAGE), NOISE))
        on("gws", "gmail", "+read", result=read_mail)
        on("gws", "auth", "status", result=Result(0, json.dumps(STATUS), NOISE))
        on("gws", "gmail", "users", "drafts", "create",
           result=Result(0, json.dumps({"id": "r-brief", "message": {"threadId": "t-b"}}), NOISE))
        on(str(self.claude), result=self.engine)
        self.log = logging.getLogger(f"test-brief-{ws.root}")
        self.log.handlers = [logging.FileHandler(str(ws.logs / "brain.log"), encoding="utf-8")]
        self.log.setLevel(logging.INFO)
        self.log.propagate = False
        self._files(at)

    def engine(self, argv):
        prompt = argv[2]
        self.prompts.append(prompt)
        if self.engine_mode == "fail":
            return Result(1, json.dumps({"type": "result", "is_error": True,
                                         "result": "Failed to authenticate: OAuth session expired"}))
        if self.engine_mode == "write":
            target = re.search(r"Write the (?:brief|review) to: (\S+)", prompt).group(1)
            (self.ws.root / target).write_text(AI_BRIEF)
        return Result(0, json.dumps({"type": "result", "subtype": "success", "is_error": False,
                                     "result": "Done.", "total_cost_usd": 0.05}))

    def _files(self, at):
        ws = self.ws
        (ws.root / "me.md").write_text("# Me\n")
        (ws.root / "strategy.md").write_text("# Strategy\n")
        (ws.briefs / "2026-10-20.md").write_text("> Generated Tue 20 Oct 07:31\n\nYesterday.\n")
        active = ws.tasks / "active"
        active.mkdir(parents=True)
        (active / "launch.md").write_text(
            "---\ntitle: Launch the spring range\npriority: P1\ndue: 2026-11-01\n---\n# Launch\n\n## Next steps\n"
            "- [ai] Draft the supplier email\n- [me] Approve the price list\n- [waiting] Samples from the factory\n"
            "- [x] Old done thing\n\n## Log\n- 2026-10-18: kicked off\n- 2026-10-20: prices drafted\n")
        (active / "notes.md").write_text("# Office move\n\n- [me] Book the van\n")
        users = {"UME": "Me Myself"}
        (ws.ledgers / "slack-users.json").write_text(json.dumps(users))

        def row(hour, minute, kind, text, from_me=False, mentions=False, day=21):
            ts = f"{int(at(2026, 10, day, hour, minute).timestamp())}.000100"
            return {"id": f"C:{ts}", "ts": ts, "time": at(2026, 10, day, hour, minute).isoformat(),
                    "channel": "C", "channel_name": "DM with Bob" if kind == "dm" else "#general", "kind": kind,
                    "user": "UME" if from_me else "U2", "user_name": "Me Myself" if from_me else "Bob",
                    "from_me": from_me, "mentions_me": mentions, "text": text, "permalink": f"https://x/{ts}",
                    "in_thread": False, "files": []}

        rows = [row(7, 50, "channel", "<@UME> please review", mentions=True),
                row(7, 55, "channel", "My own channel post", from_me=True),
                row(8, 0, "dm", "Can you sign the contract?"),
                row(8, 5, "dm", "Yes, after lunch", from_me=True),
                row(8, 10, "channel", "Shipping update"),
                row(9, 0, "channel", "Before the window", day=19)]
        (ws.slack_ledger_dir / "2026-10.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
        runs = RunsLedger(ws)
        runs.record("update_check", "error", at(2026, 10, 20, 10, 0), at(2026, 10, 20, 10, 1), "update check failed",
                    detail="HTTP 503")
        runs.record("update_check", "ok", at(2026, 10, 21, 5, 0), at(2026, 10, 21, 5, 1), "update available",
                    data={"current": "0.1.0", "latest": "0.2.0", "update_available": True})
        runs.record("health", "ok", at(2026, 10, 21, 6, 0), at(2026, 10, 21, 6, 1), "1 problem",
                    data={"problems": [{"name": "disk", "status": "warn", "message": "low disk space",
                                        "fix": "free some space"}]})
        runs.record("slack_sync", "error", at(2026, 10, 21, 8, 0), at(2026, 10, 21, 8, 1), "Slack sync failed",
                    detail="conversations.history: ratelimited")

    def ctx(self, **changes) -> JobContext:
        env = {"PATH": f"{self.ws.kit_bin}:{self.claude.parent}", "HOME": str(self.ws.root.parent), "USER": "tester"}
        ctx = JobContext(ws=self.ws, config=Config(), runner=self.runner, now=self.now, runs=RunsLedger(self.ws),
                         log=self.log, notify=lambda title, message: None, env=env, since=self.since)
        for key, value in changes.items():
            setattr(ctx, key, value)
        return ctx

    def argvs(self, *prefix):
        return [a for a in self.runner.argvs() if tuple(a[: len(prefix)]) == prefix]


@pytest.fixture
def scene(ws, at, tmp_path):
    return Scene(ws, at, tmp_path)


def decode(argv):
    raw = json.loads(argv[argv.index("--json") + 1])["message"]["raw"]
    return email.message_from_bytes(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)), policy=email_policy.default)


# ---------------------------------------------------------------- packet


def test_packet_gathers_every_source(scene):
    packet = brief.build_packet(scene.ctx())
    assert packet["window"] == {"since": "2026-10-20T07:30:00+01:00", "now": "2026-10-21T09:12:00+01:00",
                                "tz": "Europe/London"}
    assert scene.argvs("gws", "calendar")[0] == ["gws", "calendar", "+agenda", "--today", "--format", "json",
                                                 "--timezone", "Europe/London"]
    assert [e["summary"] for e in packet["calendar_today"]] == ["Supplier call", "Office closed"]
    assert packet["calendar_tomorrow"][0]["summary"] == "Standup"
    triage = scene.argvs("gws", "gmail", "+triage")[0]
    assert triage[triage.index("--max") + 1] == "40"
    assert triage[triage.index("--query") + 1] == (
        f"in:inbox -category:promotions -category:social after:{int(scene.since.timestamp())}")
    mail = packet["mail"]
    assert mail["count"] == 12 and [m["id"] for m in mail["messages"][:2]] == ["m12", "m11"]
    read = [m for m in mail["messages"] if m["excerpt"]]
    assert len(read) == 10 and len(scene.argvs("gws", "gmail", "+read")) == 10
    first = mail["messages"][0]
    assert first["thread_id"] == "t-m12" and first["link"] == "https://mail.google.com/mail/u/0/#inbox/m12"
    assert first["from"] == "Pat Doe <pat@example.com>" and first["to"] == "me@example.com"
    assert first["excerpt"].startswith("Body of m12\n\nxxx") and len(first["excerpt"]) == 1500
    assert mail["messages"][-1]["excerpt"] is None and mail["messages"][-1]["from"] == "Sender 1 <s1@example.com>"
    assert [s["text"] for s in packet["slack"]] == ["@Me Myself please review", "Can you sign the contract?",
                                                    "Yes, after lunch", "Shipping update"]
    assert packet["slack"][2]["from_me"] and packet["slack"][0]["mentions_me"]
    launch, notes = packet["tasks"]
    # Old-format files still read, with the old tags given their new names.
    assert launch == {"path": "tasks/active/launch.md", "title": "Launch the spring range", "priority": "P1",
                      "due": "2026-11-01", "last_log": "2026-10-20", "outcome": "",
                      "next_steps": ["[ai] Draft the supplier email", "[human] Approve the price list",
                                     "[external] Samples from the factory"],
                      "threads": [{"tag": "ai", "kind": "thread", "text": "Draft the supplier email"},
                                  {"tag": "human", "kind": "approval", "text": "Approve the price list"},
                                  {"tag": "external", "kind": "thread", "text": "Samples from the factory"}]}
    assert notes["title"] == "Office move" and notes["priority"] is None
    assert notes["next_steps"] == ["[human] Book the van"]
    assert packet["health"]["problems"][0]["name"] == "disk"
    assert packet["update"]["update_available"] is True
    assert [(e["job"], e["count"], e["detail"]) for e in packet["job_errors"]] == [
        ("slack_sync", 1, "conversations.history: ratelimited")]  # update_check recovered since
    assert packet["files"] == {"me": "me.md", "strategy": "strategy.md", "previous_brief": "briefs/2026-10-20.md"}
    assert packet["errors"] == []
    assert json.loads((scene.ws.packets / "2026-10-21.json").read_text()) == json.loads(json.dumps(packet))


def test_results_from_this_catchup_take_priority(scene):
    results = {"health": JobResult("ok", "all good", data={"problems": []}),
               "slack_sync": JobResult("ok", "3 new messages (1 DM, 0 mentions)")}
    packet = brief.build_packet(scene.ctx(results=results))
    assert packet["health"]["problems"] == [] and packet["job_errors"] == []
    assert packet["slack_status"] == {"status": "ok", "summary": "3 new messages (1 DM, 0 mentions)"}


def test_a_failing_source_is_isolated(scene):
    scene.runner.rules.insert(0, (("gws", "calendar"), Result(1, json.dumps({"error": {"code": 403,
                                                                                         "message": "Calendar API off"}}))))
    scene.runner.rules.insert(0, (("gws", "gmail", "+read", "--id", "m12"), Result(1, "", NOISE + "error[api]: gone\n")))
    packet = brief.build_packet(scene.ctx())
    assert [e["source"] for e in packet["errors"]] == ["calendar_today", "calendar_tomorrow"]
    assert "Calendar API off" in packet["errors"][0]["error"]
    assert packet["calendar_today"] == [] and len(packet["mail"]["messages"]) == 12 and packet["slack"]
    assert packet["mail"]["messages"][0]["excerpt"] is None and "gone" in packet["mail"]["messages"][0]["excerpt_error"]


def test_slack_is_capped_with_direct_messages_first(scene, at):
    rows = []
    for i in range(100):
        ts = f"{int(at(2026, 10, 21, 8, 0).timestamp()) + i}.000100"
        rows.append({"id": f"X:{ts}", "ts": ts, "kind": "dm" if i % 2 else "channel", "text": str(i)})
    (scene.ws.slack_ledger_dir / "2026-10.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    slack = brief.build_packet(scene.ctx())["slack"]
    assert len(slack) == brief.SLACK_CAP
    assert [s["kind"] for s in slack[:50]] == ["dm"] * 50
    assert [s["text"] for s in slack[50:]] == [str(i) for i in range(40, 100, 2)]  # the newest 30 others


# ---------------------------------------------------------------- the brief


SYNCED = {"slack_sync": JobResult("ok", "3 new messages (1 DM, 0 mentions)")}


def test_engine_brief_gets_the_stamp_and_a_gmail_draft(scene):
    result = brief.run_brief(scene.ctx(results=dict(SYNCED)))
    assert (result.status, result.summary) == ("ok", "brief ready")
    assert result.data == {"path": "briefs/2026-10-21.md", "fallback": False, "draft_id": "r-brief",
                           "cost_usd": 0.05, "window_start": "2026-10-20T07:30:00+01:00",
                           "window_end": "2026-10-21T09:12:00+01:00", "complete": True}
    assert "notified" not in result.data  # catchup sends "Your brief is ready"
    assert scene.prompts == ["/morning-brief\n\nData packet: briefs/.packets/2026-10-21.json\n"
                             "Write the brief to: briefs/2026-10-21.md\nIt covers Tue 20 Oct 07:30 to Wed 21 Oct 09:12."]
    text = (scene.ws.briefs / "2026-10-21.md").read_text()
    assert text == "> Generated Wed 21 Oct 09:12 · covers since Tue 20 Oct 07:30\n\n" + AI_BRIEF
    draft = decode(scene.argvs("gws", "gmail", "users", "drafts", "create")[0])
    assert draft["To"] == "me@example.com" and draft["Subject"] == "Brief · Wed 21 Oct"
    assert draft.get_content().replace("\r\n", "\n") == text


def test_engine_failure_gives_a_plain_fallback_brief(scene):
    scene.engine_mode = "fail"
    result = brief.run_brief(scene.ctx())
    assert (result.status, result.summary) == ("ok", "fallback brief (AI unavailable)")
    assert result.data["fallback"] is True and result.data["engine_error"] == engine.AUTH_HINT
    lines = (scene.ws.briefs / "2026-10-21.md").read_text().splitlines()
    assert lines[0] == "> Generated Wed 21 Oct 09:12 · covers since Tue 20 Oct 07:30"
    assert lines[2] == f"AI summary unavailable today: {engine.AUTH_HINT}"
    for heading in ("## Problems", "## Today", "### Tomorrow", "## Mail", "## Slack", "## Tasks"):
        assert heading in lines
    assert "- slack_sync failed: conversations.history: ratelimited" in lines
    assert "- disk: low disk space — free some space" in lines
    assert "- A kit update is available (v0.2.0): run `brain update`" in lines
    assert "- 10:00–10:30 Supplier call (Room 2)" in lines and "- all day Office closed" in lines
    assert "- 09:00–09:15 Standup" in lines
    assert "- Pat Doe <pat@example.com> — Invoice 12 overdue <https://mail.google.com/mail/u/0/#inbox/m12>" in lines
    assert "- Sender 1 <s1@example.com> — Invoice 1 overdue <https://mail.google.com/mail/u/0/#inbox/m1>" in lines
    assert any(line.startswith("- Wed 08:00 · DM with Bob · Bob: Can you sign the contract? <https://x/")
               for line in lines)
    assert "- Launch the spring range (P1, due 2026-11-01) — next: [ai] Draft the supplier email" in lines
    assert result.data["draft_id"] == "r-brief"  # the fallback still goes to drafts


def test_engine_that_writes_nothing_also_falls_back(scene):
    scene.engine_mode = "silent"
    result = brief.run_brief(scene.ctx())
    assert result.data["fallback"] and result.data["engine_error"] == "the AI didn't write the brief"


def test_draft_needs_the_compose_scope_and_the_setting(scene):
    scene.runner.rules.insert(0, (("gws", "auth", "status"),
                                  Result(0, json.dumps(dict(STATUS, scopes=[gws.GMAIL_READONLY])))))
    result = brief.run_brief(scene.ctx())
    assert result.data["draft_id"] is None and result.data["draft_note"] == "Gmail drafts permission not granted"
    assert scene.argvs("gws", "gmail", "users", "drafts", "create") == []
    ctx = scene.ctx()
    ctx.config = replace(ctx.config, brief_gmail_draft=False)
    brief.run_brief(ctx)
    brief.run_brief(scene.ctx(), draft=False)
    assert len(scene.argvs("gws", "auth", "status")) == 1  # only the first run asked


def test_a_failed_draft_is_a_note_not_an_error(scene):
    scene.runner.rules.insert(0, (("gws", "gmail", "users", "drafts", "create"),
                                  Result(1, json.dumps({"error": {"code": 500, "message": "backend error"}}))))
    result = brief.run_brief(scene.ctx())
    assert result.status == "ok" and result.data["draft_note"].startswith("Gmail draft failed")


def test_an_existing_brief_is_never_overwritten(scene):
    old = scene.ws.briefs / "2026-10-21.md"
    old.write_text("earlier brief\n")
    result = brief.run_brief(scene.ctx())
    assert result.data["path"] == "briefs/2026-10-21-2.md"
    assert old.read_text() == "earlier brief\n"
    assert (scene.ws.packets / "2026-10-21-2.json").exists()
    assert "Write the brief to: briefs/2026-10-21-2.md" in scene.prompts[0]
    packet = json.loads((scene.ws.packets / "2026-10-21-2.json").read_text())
    assert packet["files"]["previous_brief"] == "briefs/2026-10-21.md"
    assert brief.free_stem(scene.ws.briefs, "2026-10-21") == "2026-10-21-3"


def test_nothing_personal_lands_in_brain_log(scene):
    brief.run_brief(scene.ctx())
    scene.engine_mode = "fail"
    brief.run_brief(scene.ctx())
    log_text = (scene.ws.logs / "brain.log").read_text()
    assert "brief: packet ready" in log_text and "AI summary unavailable" in log_text
    for private in PRIVATE:
        assert private not in log_text, private


def test_fallback_reads_utc_event_times():
    from brainkit.brief_fallback import render_fallback

    packet = {"calendar_today": [{"start": "2026-10-21T09:00:00Z", "end": "2026-10-21T09:30:00.000Z",
                                  "summary": "Call"}]}
    lines = render_fallback(packet, "testing", "Europe/London").splitlines()
    assert "- 10:00–10:30 Call" in lines and "- Nothing to report." in lines and "- No new mail." in lines


def test_stamp_replaces_an_older_stamp():
    stamp = "> Generated Wed 21 Oct 09:12 · covers since Tue 20 Oct 07:30"
    assert brief.ensure_stamp("> Generated by the AI\n\nBody\n", stamp) == stamp + "\n\nBody\n"
    assert brief.ensure_stamp("Body", stamp) == stamp + "\n\nBody\n"


def test_job_wrapper_runs_the_brief(scene):
    result = brief_job.run(scene.ctx())
    assert result.status == "ok" and result.data["path"] == "briefs/2026-10-21.md"


def test_cli_runs_slack_sync_then_the_brief_under_the_run_lock(ws, monkeypatch, capsys):
    from brainkit import catchup
    from brainkit.cli import build_parser
    from brainkit.lock import try_lock

    calls = []

    def fake_run_due(ws_, cfg, runner, now, notify, log, runs, due, home):
        with try_lock(ws_.lock_file) as free:
            assert not free  # brain brief holds the run lock for both jobs
        calls.append(([item.name for item in due], due[1].since, cfg.brief_gmail_draft))
        return {"slack_sync": JobResult("skipped", "Slack not connected yet"),
                "brief": JobResult("ok", "brief ready", data={"path": "briefs/2026-10-21.md"})}

    monkeypatch.setattr(catchup, "_run_due", fake_run_due)
    args = build_parser().parse_args(["--workspace", str(ws.root), "brief", "--no-draft"])
    assert args.func(args) == 0
    (names, since, drafts), = calls
    assert names == ["slack_sync", "brief"] and since is not None and drafts is False
    out = capsys.readouterr().out
    assert "slack_sync: skipped — Slack not connected yet" in out and "brief: ok — brief ready" in out
    assert f"Your brief: {ws.root / 'briefs' / '2026-10-21.md'}" in out


# ---------------------------------------------------------------- window bookkeeping (review fixes)


def record(ws, result, started, finished):
    """Record a brief run the way catchup does."""
    RunsLedger(ws).record("brief", result.status, started, finished, result.summary, data=result.data)


def next_brief(ws, now):
    return {d.name: d for d in schedule.due_jobs(Config(), RunsLedger(ws), now)}["brief"]


def test_mail_arriving_while_the_brief_is_written_is_in_the_next_brief(scene, at):
    result = brief.run_brief(scene.ctx(results=dict(SYNCED)))
    assert result.data["complete"] is True and "missing" not in result.data
    assert (result.data["window_start"], result.data["window_end"]) == ("2026-10-20T07:30:00+01:00",
                                                                        "2026-10-21T09:12:00+01:00")
    record(scene.ws, result, at(2026, 10, 21, 9, 12), at(2026, 10, 21, 9, 16))  # took 4 minutes
    since = next_brief(scene.ws, at(2026, 10, 22, 7, 31)).since
    assert since == at(2026, 10, 21, 9, 12)  # where its data was gathered, not 09:16 when it finished
    packet = brief.build_packet(scene.ctx(since=since, now=at(2026, 10, 22, 7, 31)))
    assert packet["mail"]["query"].endswith(f"after:{int(at(2026, 10, 21, 9, 12).timestamp())}")


@pytest.mark.parametrize("broken,missing", [
    ("mail", ["mail"]),
    ("calendar", ["calendar_today", "calendar_tomorrow"]),
    ("slack", ["slack"]),
])
def test_a_brief_missing_a_source_is_covered_again_next_time(scene, at, broken, missing):
    results = dict(SYNCED)
    if broken == "mail":
        expired = {"error": {"code": 401, "message": "Token has been expired or revoked."}}
        scene.runner.rules.insert(0, (("gws", "gmail", "+triage"), Result(2, json.dumps(expired), NOISE)))
    elif broken == "calendar":
        scene.runner.rules.insert(0, (("gws", "calendar"), Result(1, "", NOISE + "error[api]: down\n")))
    else:
        results = {"slack_sync": JobResult("error", "Slack sync failed", detail="auth.test: token_revoked")}
    result = brief.run_brief(scene.ctx(results=results))
    assert result.status == "ok" and result.data["complete"] is False and result.data["missing"] == missing
    record(scene.ws, result, at(2026, 10, 21, 9, 12), at(2026, 10, 21, 9, 16))
    assert next_brief(scene.ws, at(2026, 10, 22, 7, 31)).since == at(2026, 10, 20, 7, 30)  # its window_start


def test_a_fallback_brief_is_incomplete_too(scene):
    scene.engine_mode = "fail"
    result = brief.run_brief(scene.ctx(results=dict(SYNCED)))
    assert result.data["fallback"] and result.data["complete"] is False and "missing" not in result.data


def test_incomplete_briefs_hold_the_window_open_up_to_the_lookback_cap(ws, at):
    runs = RunsLedger(ws)

    def brief_row(day, window_start, complete):
        started = at(2026, 10, day, 7, 30)
        runs.record("brief", "ok", started, started + timedelta(minutes=3), "brief ready",
                    data={"window_start": window_start.isoformat(), "window_end": started.isoformat(),
                          "complete": complete})

    brief_row(1, at(2026, 9, 30, 7, 30), True)
    brief_row(2, at(2026, 10, 1, 7, 30), False)  # Google login expired here
    brief_row(5, at(2026, 10, 1, 7, 30), False)  # still covering since 1 Oct
    assert next_brief(ws, at(2026, 10, 6, 7, 31)).since == at(2026, 10, 1, 7, 30)
    brief_row(20, at(2026, 10, 1, 7, 30), False)
    now = at(2026, 10, 21, 7, 31)
    assert next_brief(ws, now).since == now - timedelta(days=14)  # lookback_days_max
    brief_row(21, now - timedelta(days=14), True)  # reconnected: a complete brief closes the gap
    assert next_brief(ws, at(2026, 10, 22, 7, 31)).since == at(2026, 10, 21, 7, 30)


def test_the_slot_is_done_by_finished_at_even_when_the_window_ended_before_it(ws, at):
    runs = RunsLedger(ws)
    runs.record("brief", "ok", at(2026, 10, 20, 7, 29), at(2026, 10, 20, 7, 33), "brief ready",
                data={"window_start": at(2026, 10, 19, 7, 29).isoformat(),
                      "window_end": at(2026, 10, 20, 7, 29).isoformat(), "complete": True})
    due = schedule.due_jobs(Config(), runs, at(2026, 10, 20, 8, 0))
    assert "brief" not in [d.name for d in due]


def test_resume_point_without_window_data(ws, at):
    runs = RunsLedger(ws)
    now = at(2026, 10, 20, 15, 0)
    runs.record("brief", "ok", at(2026, 10, 20, 7, 30), at(2026, 10, 20, 7, 34))  # an older row: no data
    assert schedule.window_since(Config(), runs, "brief", now) == at(2026, 10, 20, 7, 30)  # started_at
    with ws.runs_file.open("a") as handle:
        handle.write(json.dumps({"job": "brief", "status": "ok", "started_at": "garbage",
                                 "finished_at": at(2026, 10, 20, 9, 0).isoformat(), "data": {}}) + "\n")
    assert schedule.window_since(Config(), runs, "brief", now) == at(2026, 10, 20, 9, 0)  # finished_at
    runs.record("brief", "ok", at(2026, 10, 20, 10, 0), at(2026, 10, 20, 10, 3),
                data={"window_end": at(2026, 10, 25).isoformat(), "complete": True})  # clock trouble
    assert schedule.window_since(Config(), runs, "brief", now) == at(2026, 10, 20, 10, 0)


def test_mail_estimate_and_the_fallback_line_for_a_capped_inbox(scene):
    assert brief.build_packet(scene.ctx())["mail"]["estimated_total"] == 12
    low = dict(TRIAGE, resultSizeEstimate=3)  # Gmail's estimate can be low; never below what we got
    scene.runner.rules.insert(0, (("gws", "gmail", "+triage"), Result(0, json.dumps(low), NOISE)))
    assert brief.build_packet(scene.ctx())["mail"]["estimated_total"] == 12
    big = dict(TRIAGE, resultSizeEstimate=250)
    scene.runner.rules.insert(0, (("gws", "gmail", "+triage"), Result(0, json.dumps(big), NOISE)))
    scene.engine_mode = "fail"
    brief.run_brief(scene.ctx(results=dict(SYNCED)))
    packet = json.loads((scene.ws.packets / "2026-10-21.json").read_text())
    assert packet["mail"]["estimated_total"] == 250 and packet["mail"]["count"] == 12
    lines = (scene.ws.briefs / "2026-10-21.md").read_text().splitlines()
    assert "- Showing the newest 12 of about 250 emails since Tue 20 Oct 07:30." in lines


def test_skills_use_detach_and_the_mail_estimate_rule(repo):
    skills = repo / "kit" / ".claude" / "skills"
    text = (skills / "morning-brief" / "SKILL.md").read_text()
    assert "mail.estimated_total" in text and "Showing the newest N of about M emails" in text
    for name in ("morning-brief", "onboard", "troubleshoot"):
        text = "\n".join(p.read_text() for p in sorted((skills / name).glob("*.md")))
        assert "brain brief --detach" in text and "catchup --job brief" not in text


def test_detach_starts_a_background_brief_and_returns_at_once(ws, monkeypatch, capsys):
    import subprocess
    import sys

    from brainkit import brief_cli
    from brainkit.cli import build_parser

    started = []
    monkeypatch.setattr(subprocess, "Popen", lambda argv, **kwargs: started.append((argv, kwargs)))
    args = build_parser().parse_args(["--workspace", str(ws.root), "brief", "--detach", "--no-draft"])
    assert args.func(args) == 0
    assert capsys.readouterr().out.strip() == brief_cli.DETACHED_MESSAGE
    (argv, kwargs), = started
    entry = Path(brief_cli.__file__).resolve().parents[2] / "bin" / "brain"
    assert entry.exists() and argv == [sys.executable, str(entry), "--workspace", str(ws.root), "brief", "--no-draft"]
    assert kwargs["start_new_session"] is True and kwargs["stdin"] is subprocess.DEVNULL
    assert kwargs["stdout"].name == str(ws.logs / "brief-detached.log") and kwargs["stderr"] is subprocess.STDOUT
    assert kwargs["cwd"] == str(ws.root)

    def cannot_start(argv, **kwargs):
        raise OSError("no such interpreter")

    monkeypatch.setattr(subprocess, "Popen", cannot_start)
    assert args.func(args) == 1 and "brain brief" in capsys.readouterr().out


def test_brief_exits_quietly_while_another_run_holds_the_lock(ws, monkeypatch, capsys):
    from brainkit import catchup
    from brainkit.cli import build_parser
    from brainkit.lock import try_lock

    def must_not_run(*args, **kwargs):
        raise AssertionError("ran while another run held the lock")

    monkeypatch.setattr(catchup, "_run_due", must_not_run)
    args = build_parser().parse_args(["--workspace", str(ws.root), "brief"])
    with try_lock(ws.lock_file) as held:
        assert held
        assert args.func(args) == 0
    assert "busy" in capsys.readouterr().out


# ---------------------------------------------------------------- tasks in the shared format

NEW_TASK = """---
title: "Open the second shop"
priority: "P1"
created_at: "2026-10-01"
updated_at: "2026-10-20"
due_at: "2026-12-01"
completed_at: ""
links: []
---

## Outcome

<!-- One or two sentences. -->
The second shop is open and trading.

## Pinned

- The landlord wants three months' rent up front. (2026-10-05)

## Threads / Open Loops

<!--
  - [ai] an example inside a note, never a thread
-->
- [ai] Draft the opening-day email
- [human] Q: Do we open on a Saturday or a Monday?
  Context: Saturdays are busier but the fitters finish on Friday night.
  Options: Saturday :: more customers | Monday :: a calm first day
  Recommend: Saturday
- [external] Signed lease from the landlord
  Follow-Up: 2026-10-28
- Deferred (on the lease being signed; review by 2026-11-15): order the shop sign

## Activity

### 2026-10-20 14:05 UTC
- Fitters booked.

### 2026-10-12 09:00 UTC
- Viewed the unit.
"""


def test_new_format_task_in_the_packet(ws):
    (ws.tasks / "active").mkdir(parents=True)
    (ws.tasks / "active" / "shop.md").write_text(NEW_TASK)
    (ws.tasks / "active" / "TEMPLATE.md").write_text(NEW_TASK)
    [task] = brief.collect_tasks(ws)
    assert task["path"] == "tasks/active/shop.md" and task["title"] == "Open the second shop"
    assert (task["priority"], task["due"], task["last_log"]) == ("P1", "2026-12-01", "2026-10-20")
    assert task["outcome"] == "The second shop is open and trading."
    assert task["next_steps"] == [
        "[ai] Draft the opening-day email", "[human] Q: Do we open on a Saturday or a Monday?",
        "[external] Signed lease from the landlord",
        "Deferred (on the lease being signed; review by 2026-11-15): order the shop sign"]
    assert task["threads"][1] == {
        "tag": "human", "kind": "question", "text": "Q: Do we open on a Saturday or a Monday?",
        "context": "Saturdays are busier but the fitters finish on Friday night.",
        "options": "Saturday :: more customers | Monday :: a calm first day", "recommend": "Saturday"}
    assert task["threads"][2]["follow_up"] == "2026-10-28"
    assert task["threads"][3]["kind"] == "deferred"


def test_fallback_brief_lists_a_new_format_task(ws):
    (ws.tasks / "active").mkdir(parents=True)
    (ws.tasks / "active" / "shop.md").write_text(NEW_TASK)
    packet = {"tasks": brief.collect_tasks(ws)}
    text = brief.render_fallback(packet, "no engine", "Europe/London")
    assert "- Open the second shop (P1, due 2026-12-01) — next: [ai] Draft the opening-day email" in text
