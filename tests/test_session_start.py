"""`brain _session-start`: the Claude Code SessionStart hook that tells a chat about kit updates."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

from brainkit import session_start
from brainkit.cli import build_parser
from brainkit.cli import main as cli_main
from brainkit.jobs.base import JobResult
from brainkit.runner import FakeRunner
from brainkit.runs import RunsLedger
from release_helpers import FakeNet, github_release, write

API = "https://api.github.com/repos/fredn3y/brain-starter/releases/latest"
LINE = ('A brain kit update is available (v0.2.0 -> v0.2.1: Faster briefs). Tell the user in one '
        'line and offer to run it; if they say yes, run "brain update --yes".')


@pytest.fixture
def brain(ws):
    write(ws.version_file, "0.2.0\n")
    return ws


class Check:
    """Stands in for jobs.update_check.check and counts the calls."""

    def __init__(self, latest="0.2.1", notes="## Faster briefs\n\nMore detail", status="ok"):
        self.calls = []
        self.latest, self.notes, self.status = latest, notes, status

    def __call__(self, ws, runner, repo, timeout=30):
        self.calls.append((repo, timeout))
        if self.status != "ok":
            return JobResult("error", "update check failed", detail="curl failed (6)")
        available = self.latest != "0.2.0"
        return JobResult("ok", "update available" if available else "up to date",
                         data={"current": "0.2.0", "latest": self.latest, "update_available": available,
                               "notes": self.notes, "html_url": ""})


def test_first_session_checks_records_and_tells_once(brain, at):
    check, now = Check(), at(2026, 11, 12, 9, 0)
    assert session_start.update_line(brain, FakeRunner(), now, check=check) == LINE
    assert check.calls == [("fredn3y/brain-starter", session_start.NETWORK_TIMEOUT)]
    row = RunsLedger(brain).last("update_check")
    assert row["data"]["latest"] == "0.2.1" and row["data"]["source"] == "session-start"
    # A second chat the same day: no network, and the user isn't asked again.
    assert session_start.update_line(brain, FakeRunner(), now + timedelta(hours=3), check=check) == ""
    assert len(check.calls) == 1
    # A day later: checked again and told again.
    assert session_start.update_line(brain, FakeRunner(), now + timedelta(hours=25), check=check) == LINE
    assert len(check.calls) == 2


def test_a_recent_scheduled_check_is_reused(brain, at):
    now = at(2026, 11, 12, 9, 0)
    RunsLedger(brain).record("update_check", "ok", now - timedelta(hours=2), now - timedelta(hours=2),
                             data={"latest": "0.2.1", "update_available": True, "notes": "Faster briefs"})
    check = Check()
    assert session_start.update_line(brain, FakeRunner(), now, check=check) == LINE
    assert check.calls == []


def test_up_to_date_or_already_updated_prints_nothing(brain, at):
    now = at(2026, 11, 12, 9, 0)
    assert session_start.update_line(brain, FakeRunner(), now, check=Check(latest="0.2.0")) == ""
    RunsLedger(brain).record("update_check", "ok", now, now,
                             data={"latest": "0.2.0", "update_available": True})  # from before an update
    assert session_start.update_line(brain, FakeRunner(), now, check=Check()) == ""


def test_offline_prints_nothing_records_no_error_and_waits(brain, at):
    now = at(2026, 11, 12, 9, 0)
    offline = Check(status="error")
    assert session_start.update_line(brain, FakeRunner(), now, check=offline) == ""
    assert RunsLedger(brain).rows() == []  # an offline laptop must not put errors in the brief
    state = json.loads((brain.state / "session-start.json").read_text())
    assert "checked_at" in state
    assert session_start.update_line(brain, FakeRunner(), now + timedelta(minutes=30), check=offline) == ""
    assert len(offline.calls) == 1  # not again in every chat
    assert session_start.update_line(brain, FakeRunner(), now + timedelta(minutes=61), check=offline) == ""
    assert len(offline.calls) == 2  # but a failed try is repeated after an hour, not a day


def test_update_check_hours_comes_from_the_config(brain, at):
    write(brain.config_file, json.dumps({"update_check_hours": 2}))
    check, now = Check(), at(2026, 11, 12, 9, 0)
    session_start.update_line(brain, FakeRunner(), now, check=check)
    session_start.update_line(brain, FakeRunner(), now + timedelta(hours=3), check=check)
    assert len(check.calls) == 2


def test_headline():
    assert session_start.headline("## What's new\nmore") == "What's new"
    assert session_start.headline("\n\n- **Faster briefs**\n") == "Faster briefs"
    assert session_start.headline("") == ""
    assert session_start.headline("a\x07b " + "x" * 300).endswith("…")
    assert len(session_start.headline("x" * 300)) == 120


def test_cli_end_to_end_with_fake_github(brain, monkeypatch, capsys):
    fake = FakeRunner()
    FakeNet(fake).api[API] = (200, github_release("0.2.1", notes="Faster briefs\nMore"))
    monkeypatch.setattr(session_start, "Runner", lambda: fake)
    assert cli_main(["--workspace", str(brain.root), "_session-start"]) == 0
    assert capsys.readouterr().out == LINE + "\n"
    config = next(c["input"] for c in fake.calls if c["argv"][:3] == ["curl", "--config", "-"])
    assert f"max-time = {session_start.NETWORK_TIMEOUT}" in config
    assert cli_main(["--workspace", str(brain.root), "_session-start"]) == 0
    assert capsys.readouterr().out == ""


def test_cli_always_exits_zero_and_stays_quiet(brain, monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(session_start, "update_line", boom)
    assert cli_main(["--workspace", str(brain.root), "_session-start"]) == 0
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""
    monkeypatch.setattr(session_start, "update_line", lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt()))
    assert cli_main(["--workspace", str(brain.root), "_session-start"]) == 0
    monkeypatch.undo()
    offline = FakeRunner()  # every command fails: curl too
    monkeypatch.setattr(session_start, "Runner", lambda: offline)
    assert cli_main(["--workspace", str(brain.root), "_session-start"]) == 0
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""


def test_scheduled_runs_stay_silent_and_do_not_use_up_the_notice(brain, monkeypatch, capsys):
    monkeypatch.setenv("BRAIN_ENGINE_RUN", "1")
    monkeypatch.setattr(session_start, "update_line",
                        lambda *a, **k: pytest.fail("a scheduled run must not check or tell"))
    assert cli_main(["--workspace", str(brain.root), "_session-start"]) == 0
    assert capsys.readouterr().out == ""
    assert not (brain.state / "session-start.json").exists()


def test_hidden_from_help():
    assert "_session-start" not in build_parser().format_help()
