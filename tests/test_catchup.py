"""Catch-up runner (with fake job modules), plus its helpers: lock and notify."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from brainkit import catchup, cli
from brainkit import jobs as jobs_pkg
from brainkit import notify as notify_mod
from brainkit.clock import parse_iso, tz
from brainkit.config import Config
from brainkit.jobs.base import JobResult
from brainkit.lock import try_lock
from brainkit.log import get_logger
from brainkit.runner import FakeRunner, Result
from brainkit.runs import RunsLedger

HOME = "/Users/someone"


class FakeJobs:
    """Stands in for brainkit.jobs.<name> modules."""

    def __init__(self):
        self.outcomes = {}
        self.missing = set()
        self.calls = []

    def runner(self, name):
        if name in self.missing:
            raise ImportError(f"No module named 'brainkit.jobs.{name}'")

        def run(ctx):
            self.calls.append((name, ctx))
            outcome = self.outcomes.get(name, JobResult("ok", summary=f"{name} done"))
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

        return run

    def names(self):
        return [name for name, _ in self.calls]

    def ctx(self, name):
        return next(ctx for job, ctx in self.calls if job == name)


class Notes:
    def __init__(self):
        self.sent = []

    def __call__(self, title, message):
        self.sent.append((title, message))

    def messages(self):
        return [message for _, message in self.sent]


@pytest.fixture
def fake_jobs(monkeypatch):
    jobs = FakeJobs()
    monkeypatch.setattr(jobs_pkg, "job_runner", jobs.runner)
    return jobs


@pytest.fixture
def notes():
    return Notes()


@pytest.fixture
def log(ws):
    return get_logger(ws, name=f"brain-test-{uuid.uuid4().hex}")


def go(ws, now, notes, log, **kwargs):
    config = kwargs.pop("config", Config())
    return catchup.run_catchup(ws, config, FakeRunner(), now, notes, log, home=HOME, **kwargs)


# ---------------------------------------------------------------- runner


def test_runs_due_jobs_in_order_and_records_every_run(ws, at, fake_jobs, notes, log):
    assert go(ws, at(2026, 10, 19, 9, 12), notes, log) == 0
    assert fake_jobs.names() == jobs_pkg.JOB_ORDER
    rows = RunsLedger(ws).rows()
    assert [row["job"] for row in rows] == jobs_pkg.JOB_ORDER + ["catchup"]
    assert all(row["status"] == "ok" for row in rows)
    assert rows[-1]["summary"] == "ran: health, slack_sync, update_check, brief, weekly_review"
    assert rows[-1]["data"]["jobs"]["brief"] == "ok"


def test_context_carries_since_results_and_environment(ws, at, fake_jobs, notes, log):
    now = at(2026, 10, 20, 9, 12)
    go(ws, now, notes, log)
    brief = fake_jobs.ctx("brief")
    assert brief.since == now - timedelta(hours=24)
    assert list(brief.results) == ["health", "slack_sync", "update_check"]
    assert brief.now == now and brief.ws is ws
    assert fake_jobs.ctx("health").since is None and fake_jobs.ctx("health").results == {}
    env = brief.env
    assert env["PATH"].split(":") == [str(ws.kit_bin), f"{HOME}/.local/bin", "/opt/homebrew/bin",
                                      "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"]
    assert env["HOME"] == HOME and env["BRAIN_HOME"] == str(ws.root) and env["USER"]


def test_failure_is_isolated_and_reported(ws, at, fake_jobs, notes, log):
    fake_jobs.outcomes["slack_sync"] = RuntimeError("boom")
    assert go(ws, at(2026, 10, 19, 9, 12), notes, log) == 0
    assert fake_jobs.names() == jobs_pkg.JOB_ORDER  # later jobs still ran
    rows = {row["job"]: row for row in RunsLedger(ws).rows()}
    assert rows["slack_sync"]["status"] == "error" and rows["slack_sync"]["detail"] == "RuntimeError: boom"
    assert rows["catchup"]["status"] == "error"
    assert rows["catchup"]["summary"].endswith("; 1 error")
    assert notes.messages() == ["Catching up since Sun 09:12…",  # first run covers the last 24 h
                                "Catch-up finished with 1 problem — they're at the top of your brief"]


def test_error_detail_is_short_one_line_and_scrubbed(ws, at, fake_jobs, notes, log):
    fake_jobs.outcomes["health"] = ValueError("token xoxb-1234-5678-abcdef leaked\n" + "y" * 2000)
    go(ws, at(2026, 10, 20, 9, 12), notes, log)
    detail = RunsLedger(ws).last("health", status="error")["detail"]
    assert len(detail) <= catchup.DETAIL_LIMIT and "\n" not in detail
    assert "xoxb-1234" not in detail and "<slack-token>" in detail
    assert "Traceback" not in detail


def test_logged_traceback_is_scrubbed(ws, at, fake_jobs, notes, log):
    fake_jobs.outcomes["health"] = RuntimeError("leaked xoxb-9999-8888-secretpart")
    go(ws, at(2026, 10, 20, 9, 12), notes, log)
    text = (ws.logs / "brain.log").read_text()
    assert "Traceback" in text and "<slack-token>" in text
    assert "xoxb-9999" not in text


def test_missing_job_module_is_recorded_and_others_continue(ws, at, fake_jobs, notes, log):
    fake_jobs.missing.add("slack_sync")
    go(ws, at(2026, 10, 20, 9, 12), notes, log)
    row = RunsLedger(ws).last("slack_sync", status="error")
    assert row["summary"] == "job not available" and "job not available" in row["detail"]
    assert "brief" in fake_jobs.names()


def test_job_module_that_fails_to_load_is_recorded(ws, at, fake_jobs, notes, log, monkeypatch):
    def loader(name):
        if name == "brief":
            raise SyntaxError("invalid syntax (brief.py, line 3)")
        return fake_jobs.runner(name)

    monkeypatch.setattr(jobs_pkg, "job_runner", loader)
    assert go(ws, at(2026, 10, 19, 9, 12), notes, log) == 0
    row = RunsLedger(ws).last("brief", status="error")
    assert row["summary"] == "job couldn't load" and "SyntaxError" in row["detail"]
    assert "weekly_review" in fake_jobs.names()


def test_exploding_notifier_does_not_break_the_run(ws, at, fake_jobs, log):
    def explode(title, message):
        raise RuntimeError("no notifications here")

    assert catchup.run_catchup(ws, Config(), FakeRunner(), at(2026, 10, 20, 9, 12), explode, log, home=HOME) == 0
    assert RunsLedger(ws).last("catchup") is not None


def test_ledger_times_are_right_across_the_autumn_clock_change(ws, fake_jobs, notes, log, monkeypatch):
    # began, run start, job start, job end (+2 min), catch-up end
    ticks = iter([1000.0, 1000.0, 1000.0, 1120.0, 1120.0])
    monkeypatch.setattr(catchup, "_wall_clock", lambda: next(ticks))
    now = datetime(2026, 10, 25, 0, 59, tzinfo=timezone.utc).astimezone(tz("Europe/London"))  # 01:59 BST
    go(ws, now, notes, log, only="health")
    row = RunsLedger(ws).last("health")
    assert parse_iso(row["started_at"]) == datetime(2026, 10, 25, 0, 59, tzinfo=timezone.utc)
    assert parse_iso(row["finished_at"]) == datetime(2026, 10, 25, 1, 1, tzinfo=timezone.utc)  # 01:01 GMT


def test_unexpected_return_value_is_an_error(ws, at, fake_jobs, notes, log):
    fake_jobs.outcomes["update_check"] = "not a JobResult"
    go(ws, at(2026, 10, 20, 9, 12), notes, log)
    assert RunsLedger(ws).last("update_check", status="error") is not None


def test_job_data_is_made_json_safe(ws, at, fake_jobs, notes, log):
    now = at(2026, 10, 20, 9, 12)
    fake_jobs.outcomes["health"] = JobResult("ok", data={"when": now})
    go(ws, now, notes, log)
    assert isinstance(RunsLedger(ws).last("health")["data"]["when"], str)


def test_second_run_finds_nothing_due_and_stays_silent(ws, at, fake_jobs, notes, log):
    now = at(2026, 10, 20, 9, 12)
    go(ws, now, notes, log)
    rows_before, calls_before, notes_before = len(RunsLedger(ws).rows()), len(fake_jobs.calls), len(notes.sent)
    assert go(ws, now + timedelta(minutes=1), notes, log) == 0
    assert len(RunsLedger(ws).rows()) == rows_before
    assert len(fake_jobs.calls) == calls_before and len(notes.sent) == notes_before


def test_busy_lock_exits_zero_and_runs_nothing(ws, at, fake_jobs, notes, log):
    with try_lock(ws.lock_file) as held:
        assert held
        assert go(ws, at(2026, 10, 20, 9, 12), notes, log) == 0
    assert fake_jobs.calls == [] and RunsLedger(ws).rows() == [] and notes.sent == []


def test_only_forces_one_job(ws, at, fake_jobs, notes, log, capsys):
    now = at(2026, 10, 20, 9, 12)
    go(ws, now, notes, log)
    fake_jobs.calls.clear()
    assert go(ws, now + timedelta(minutes=5), notes, log, only="brief") == 0
    assert fake_jobs.names() == ["brief"]
    assert fake_jobs.ctx("brief").since is not None
    assert "brief: ok" in capsys.readouterr().out


def test_only_returns_one_when_that_job_fails(ws, at, fake_jobs, notes, log, capsys):
    fake_jobs.outcomes["health"] = RuntimeError("disk on fire")
    assert go(ws, at(2026, 10, 20, 9, 12), notes, log, only="health") == 1
    out = capsys.readouterr().out
    assert "health: error" in out and "disk on fire" in out


def test_only_rejects_unknown_job(ws, at, fake_jobs, notes, log):
    assert go(ws, at(2026, 10, 20, 9, 12), notes, log, only="nope") == 2
    assert fake_jobs.calls == []


def test_dry_run_prints_plan_and_touches_nothing(ws, at, fake_jobs, notes, log, capsys):
    assert go(ws, at(2026, 10, 20, 9, 12), notes, log, dry_run=True) == 0
    out = capsys.readouterr().out
    assert "Due now (Tue 09:12)" in out and "never run" in out and "covers since Mon 09:12" in out
    assert fake_jobs.calls == [] and RunsLedger(ws).rows() == [] and notes.sent == []
    assert not ws.lock_file.exists()


def test_dry_run_with_nothing_due(ws, at, fake_jobs, notes, log, capsys):
    now = at(2026, 10, 20, 9, 12)
    go(ws, now, notes, log)
    capsys.readouterr()
    go(ws, now + timedelta(minutes=1), notes, log, dry_run=True)
    assert "Nothing is due right now" in capsys.readouterr().out


def test_brief_ready_notification(ws, at, fake_jobs, notes, log):
    go(ws, at(2026, 10, 20, 9, 12), notes, log)
    assert notes.messages()[-1] == "Your brief is ready"


def test_brief_that_notified_itself_is_not_announced_twice(ws, at, fake_jobs, notes, log):
    fake_jobs.outcomes["brief"] = JobResult("ok", data={"notified": True})
    go(ws, at(2026, 10, 20, 9, 12), notes, log, config=Config(weekly_review=False))
    assert notes.messages() == ["Catching up since Mon 09:12…"]


def test_failed_brief_points_at_the_next_brief(ws, at, fake_jobs, notes, log):
    fake_jobs.outcomes["brief"] = RuntimeError("engine down")
    go(ws, at(2026, 10, 20, 9, 12), notes, log, config=Config(weekly_review=False))
    assert notes.messages()[-1] == "Catch-up finished with 1 problem — they'll be at the top of your next brief"


def test_hourly_run_without_brief_stays_quiet_even_on_error(ws, at, fake_jobs, notes, log):
    now = at(2026, 10, 20, 9, 12)
    go(ws, now, notes, log)
    notes.sent.clear()
    fake_jobs.calls.clear()
    fake_jobs.outcomes["slack_sync"] = RuntimeError("slack down")
    go(ws, now + timedelta(hours=1, minutes=1), notes, log)  # slack interval passed, brief done
    assert fake_jobs.names() == ["slack_sync"]
    assert notes.sent == []
    assert RunsLedger(ws).last("slack_sync", status="error")["detail"] == "RuntimeError: slack down"


def test_long_gap_start_message_includes_the_date(ws, at, fake_jobs, notes, log):
    runs = RunsLedger(ws)
    runs.record("brief", "ok", at(2026, 10, 5, 7, 30), at(2026, 10, 5, 7, 31))
    go(ws, at(2026, 10, 20, 9, 12), notes, log)
    assert notes.messages()[0] == "Catching up since Tue 6 Oct 09:12…"  # capped at 14 days


def test_settings_problems_are_logged_not_fatal(ws, at, fake_jobs, notes, log):
    go(ws, at(2026, 10, 20, 9, 12), notes, log, config=Config(brief_time="half seven"))
    assert "brief" in fake_jobs.names()
    assert "brief_time" in (ws.logs / "brain.log").read_text()


def test_rotate_launchd_log(ws):
    (ws.logs / "launchd.log").write_text("x" * (catchup.LAUNCHD_LOG_MAX + 1))
    (ws.logs / "launchd.log.1").write_text("one")
    (ws.logs / "launchd.log.2").write_text("two")
    catchup.rotate_launchd_log(ws)
    assert not (ws.logs / "launchd.log").exists()
    assert (ws.logs / "launchd.log.2").read_text() == "one"
    assert (ws.logs / "launchd.log.3").read_text() == "two"
    (ws.logs / "launchd.log").write_text("small")
    catchup.rotate_launchd_log(ws)
    assert (ws.logs / "launchd.log").read_text() == "small"


def test_cli_dry_run(ws, capsys):
    assert cli.main(["--workspace", str(ws.root), "catchup", "--dry-run"]) == 0
    assert "Due now" in capsys.readouterr().out


# ---------------------------------------------------------------- lock


def test_try_lock_is_exclusive_and_released(ws):
    with try_lock(ws.lock_file) as first:
        assert first
        with try_lock(ws.lock_file) as second:
            assert not second
        assert ws.lock_file.read_text().strip().isdigit()
    with try_lock(ws.lock_file) as again:
        assert again


def test_try_lock_released_when_body_raises(ws):
    with pytest.raises(RuntimeError):
        with try_lock(ws.lock_file):
            raise RuntimeError("crash")
    with try_lock(ws.lock_file) as held:
        assert held


# ---------------------------------------------------------------- notify


def test_notify_is_a_no_op_off_mac():
    fake = FakeRunner()
    assert notify_mod.notify(fake, "Brain", "hello", platform="linux") is None
    assert fake.calls == []


def test_notify_escapes_quotes_backslashes_and_newlines():
    fake = FakeRunner().on("osascript", result=Result(0))
    notify_mod.notify(fake, 'The "brain"', 'Say "hi" \\ there\nnow', platform="darwin")
    assert fake.argvs() == [["osascript", "-e",
                             'display notification "Say \\"hi\\" \\\\ there now" with title "The \\"brain\\""']]


def test_notify_truncates_long_messages():
    fake = FakeRunner().on("osascript", result=Result(0))
    notify_mod.notify(fake, "Brain", "a" * 500, platform="darwin")
    script = fake.argvs()[0][2]
    body = script.split('"')[1]
    assert len(body) == notify_mod.MESSAGE_LIMIT and body.endswith("…")


def test_make_notifier_never_raises(ws, log):
    class Exploding(FakeRunner):
        def run(self, argv, input=None, env=None, timeout=None, cwd=None):
            raise OSError("no osascript")

    send = notify_mod.make_notifier(Exploding(), log, platform="darwin")
    send("Brain", "hello")  # must not raise
    failing = notify_mod.make_notifier(FakeRunner().on("osascript", result=Result(1)), log, platform="darwin")
    failing("Brain", "hello")
    text = (ws.logs / "brain.log").read_text()
    assert "notification failed" in text and "notification not shown" in text


def test_notify_test_command(monkeypatch, capsys):
    fake = FakeRunner().on("osascript", result=Result(0))
    monkeypatch.setattr(notify_mod, "_runner", lambda: fake)
    monkeypatch.setattr(notify_mod, "_platform", lambda: "darwin")
    assert cli.main(["notify-test"]) == 0
    assert notify_mod.TEST_HINT in capsys.readouterr().out
    assert fake.argvs()[0][:2] == ["osascript", "-e"]


def test_notify_test_command_off_mac(monkeypatch, capsys):
    monkeypatch.setattr(notify_mod, "_platform", lambda: "linux")
    assert cli.main(["notify-test"]) == 2
