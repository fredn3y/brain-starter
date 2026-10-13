"""`brain report`: redaction rules and a zip that holds only the allowed diagnostics."""

from __future__ import annotations

import json
import re
import sys
import zipfile

import pytest

from brainkit import cli, doctor, report
from brainkit.config import Config
from brainkit.runner import FakeRunner, Result
from brainkit.runs import RunsLedger

TOKEN = "xoxb-1234-5678-abcdefSECRET"
EMAIL = "someone@example.com"
PRIVATE = ["BRIEF-CONTENT-DO-NOT-SHIP", "TASK-CONTENT-DO-NOT-SHIP", "SLACK-MESSAGE-DO-NOT-SHIP",
           "ME-CONTENT-DO-NOT-SHIP", "PACKET-CONTENT-DO-NOT-SHIP"]


@pytest.mark.parametrize("text, expected", [
    (f"mail {EMAIL} now", "mail <email> now"),
    ("token xoxb-123-456-abc end", "token <slack-token> end"),
    ("xoxp-1-2-3", "<slack-token>"),
    ("Bearer ya29.a0AfH6SMB-xyz_123.abc", "Bearer <google-token>"),
    ("key sk-abcdefghijklmnop1234 end", "key <api-key> end"),
    ("-----BEGIN PRIVATE KEY-----\nMIIabc\nxyz\n-----END PRIVATE KEY----- after", "<pem> after"),
    ("hash " + "a1" * 20, "hash <redacted>"),
    ("/Users/jane/brain/logs/brain.log", "/Users/<user>/brain/logs/brain.log"),
    ("short words and 2026-10-20T09:12:00+01:00 stay", "short words and 2026-10-20T09:12:00+01:00 stay"),
])
def test_redact(text, expected):
    assert report.redact(text) == expected


def test_redact_home_and_idempotence():
    text = f"/home/someone/brain/x {TOKEN} /Users/other/y"
    once = report.redact(text, home="/home/someone/")
    assert once == "~/brain/x <slack-token> /Users/<user>/y"
    assert report.redact(once, home="/home/someone") == once


def test_redact_secrets_leaves_emails_and_paths():
    assert report.redact_secrets(f"{EMAIL} {TOKEN} /Users/jane") == f"{EMAIL} <slack-token> /Users/jane"


def private_workspace(ws):
    (ws.briefs / "2026-10-20.md").write_text(PRIVATE[0])
    (ws.tasks / "active").mkdir(parents=True)
    (ws.tasks / "active" / "launch.md").write_text(PRIVATE[1])
    (ws.slack_ledger_dir / "2026-10.jsonl").write_text(json.dumps({"text": PRIVATE[2]}) + "\n")
    (ws.root / "me.md").write_text(PRIVATE[3])
    (ws.packets / "2026-10-20.json").write_text(PRIVATE[4])


def zip_text(path):
    with zipfile.ZipFile(path) as archive:
        return archive.namelist(), {name: archive.read(name).decode("utf-8") for name in archive.namelist()}


@pytest.fixture
def no_slack_api(monkeypatch):
    monkeypatch.setitem(sys.modules, "brainkit.slack_api", None)


def test_report_holds_only_allowed_redacted_files(ws, at, tmp_path, no_slack_api):
    private_workspace(ws)
    runs = RunsLedger(ws)
    runs.record("health", "error", at(2026, 10, 20, 9, 0), at(2026, 10, 20, 9, 1), summary=f"ping {EMAIL}",
                detail=f"failed using {TOKEN}", data={"note": TOKEN, "list": [EMAIL]})
    (ws.logs / "brain.log").write_text(f"2026-10-20 INFO token={TOKEN} for {EMAIL}\n")
    (ws.logs / "launchd.log").write_text("Traceback: /Users/jane/brain/.kit/lib/brainkit/x.py\n")
    fake = FakeRunner().on("claude", "--version", result=Result(0, "2.1.300 (Claude Code)\n"))
    path = report.make_report(ws, Config(), fake, at(2026, 10, 20, 9, 12), platform="linux",
                              home=str(tmp_path / "home"))
    assert path.name == "brain-report-20261020-0912.zip" and path.parent == ws.reports
    names, files = zip_text(path)
    assert names == list(report.ALLOWED_FILES)
    everything = "\n".join(files.values())
    for secret in PRIVATE + [TOKEN, EMAIL, "/Users/jane"]:
        assert secret not in everything
    assert "claude: 2.1.300 (Claude Code)" in files["summary.txt"]
    assert "codex: not installed" in files["summary.txt"]
    row = json.loads(files["runs.jsonl"].splitlines()[0])
    assert row["detail"] == "failed using <slack-token>" and row["data"]["list"] == ["<email>"]
    assert json.loads(files["config.json"])["brief_time"] == "07:30"
    assert isinstance(json.loads(files["doctor.json"]), list)
    assert not list(ws.reports.glob("*.tmp"))


def test_report_tails_logs_across_rotation(ws, at, tmp_path, no_slack_api):
    (ws.logs / "brain.log.1").write_text("".join(f"old {i}\n" for i in range(400)))
    (ws.logs / "brain.log").write_text("".join(f"new {i}\n" for i in range(300)))
    path = report.make_report(ws, Config(), FakeRunner(), at(2026, 10, 20, 9, 12), platform="linux",
                              home=str(tmp_path))
    lines = zip_text(path)[1]["brain.log"].splitlines()
    assert len(lines) == report.LOG_TAIL_LINES
    assert lines[0] == "old 200" and lines[-1] == "new 299"


def test_report_keeps_last_hundred_runs(ws, at, tmp_path, no_slack_api):
    runs = RunsLedger(ws)
    for i in range(150):
        runs.record("health", "ok", at(2026, 10, 20, 9, 0), at(2026, 10, 20, 9, 1), summary=f"run {i}")
    lines = zip_text(report.make_report(ws, Config(), FakeRunner(), at(2026, 10, 20, 9, 12),
                                        platform="linux", home=str(tmp_path)))[1]["runs.jsonl"].splitlines()
    assert len(lines) == 100 and json.loads(lines[-1])["summary"] == "run 149"


def test_report_reveals_in_finder_on_mac_only(ws, at, tmp_path, no_slack_api):
    fake = FakeRunner().on("open", result=Result(1, "", "no Finder here"))
    path = report.make_report(ws, Config(), fake, at(2026, 10, 20, 9, 12), platform="darwin", home=str(tmp_path))
    assert ["open", "-R", str(path)] in fake.argvs()
    other = FakeRunner()
    report.make_report(ws, Config(), other, at(2026, 10, 20, 9, 13), platform="linux", home=str(tmp_path))
    assert not any(argv[0] == "open" for argv in other.argvs())


def test_report_survives_a_doctor_crash(ws, at, tmp_path, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError(f"doctor exploded near {TOKEN}")

    monkeypatch.setattr(doctor, "run_checks", broken)
    path = report.make_report(ws, Config(), FakeRunner(), at(2026, 10, 20, 9, 12), platform="linux",
                              home=str(tmp_path))
    checks = json.loads(zip_text(path)[1]["doctor.json"])
    assert checks[0]["status"] == "warn" and "doctor could not run" in checks[0]["message"]
    assert TOKEN not in checks[0]["message"]


def test_cli_report(ws, tmp_path, monkeypatch, capsys, no_slack_api):
    monkeypatch.setattr(report, "_runner", lambda: FakeRunner())
    monkeypatch.setattr(report, "_platform", lambda: "linux")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert cli.main(["--workspace", str(ws.root), "report"]) == 0
    first, second = capsys.readouterr().out.strip().splitlines()
    assert re.search(r"brain-report-\d{8}-\d{4}\.zip$", first)
    assert zipfile.is_zipfile(first)
    assert "consultant" in second
