"""`brain doctor` checks against a faked Mac: ok / warn / fail paths and the never-crash rule."""

from __future__ import annotations

import json
import os
import sys
import types
from collections import namedtuple

import pytest

from brainkit import cli, doctor
from brainkit import secrets as keychain
from brainkit.config import Config
from brainkit.paths import Workspace
from brainkit.runner import FakeRunner, Result
from brainkit.runs import RunsLedger

Usage = namedtuple("Usage", "total used free")
GB = 1024 ** 3
TOKEN = "xoxp-1111-2222-3333-supersecrettoken"
SCOPE = "https://www.googleapis.com/auth/"
GOOD_GWS = {"token_valid": True, "has_refresh_token": True, "user": "someone@example.com",
            "scopes": [SCOPE + s for s in ("gmail.readonly", "gmail.compose", "calendar.readonly", "drive.readonly")]}
LAUNCHCTL_PRINT = "gui/501/local.brain-starter.catchup = {\n\tstate = not running\n\tlast exit code = 0\n}\n"


def healthy_runner():
    fake = FakeRunner()
    fake.on("sw_vers", "-productVersion", result=Result(0, "14.5\n"))
    fake.on("uname", "-m", result=Result(0, "arm64\n"))
    fake.on("xcode-select", "-p", result=Result(0, "/Library/Developer/CommandLineTools\n"))
    fake.on("claude", "--version", result=Result(0, "2.1.300 (Claude Code)\n"))
    fake.on("claude", "auth", "status", result=Result(0, '{"loggedIn": true, "authMethod": "claude.ai"}'))
    fake.on("codex", "--version", result=Result(0, "codex-cli 1.2.3\n"))
    fake.on("codex", "login", "status", result=Result(0, "Logged in using ChatGPT\n"))
    fake.on("gws", "--version", result=Result(0, "gws 0.22.5\n"))
    fake.on("gws", "auth", "status", result=Result(0, json.dumps(GOOD_GWS)))
    fake.on("security", "find-generic-password",
            result=lambda argv: Result(0, TOKEN + "\n") if "-w" in argv else Result(0, "attributes\n"))
    fake.on("launchctl", "print", result=Result(0, LAUNCHCTL_PRINT))
    fake.on("tmutil", "destinationinfo", result=Result(0, "====\nName          : Backup\nKind          : Local\n"))
    fake.on("profiles", "status", "-type", "enrollment",
            result=Result(0, "Enrolled via DEP: No\nMDM enrollment: No\n"))
    return fake


def override(fake, *prefix, result):
    fake.rules.insert(0, (tuple(prefix), result))
    return fake


class Mac:
    """A fake home + workspace + runner where every check passes."""

    def __init__(self, tmp_path, at):
        self.home = tmp_path / "home"
        self.ws = Workspace(self.home / "brain")
        self.ws.root.mkdir(parents=True)
        self.ws.ensure_dirs()
        self.ws.tasks.mkdir()
        self.now = at(2026, 10, 20, 10, 0)  # a Tuesday
        RunsLedger(self.ws).record("catchup", "ok", at(2026, 10, 20, 9, 0), at(2026, 10, 20, 9, 1), summary="ran: health")
        self.trust(True)
        (self.home / ".codex" / "rules").mkdir(parents=True)
        (self.home / ".codex" / "rules" / "brain-starter.rules").write_text("rules")
        (self.home / "Applications" / "T3 Code (Alpha).app").mkdir(parents=True)
        self.runner = healthy_runner()

    def trust(self, accepted):
        data = {"projects": {str(self.ws.root): {"hasTrustDialogAccepted": accepted}}, "oauthAccount": {"x": 1}}
        (self.home / ".claude.json").write_text(json.dumps(data))

    def checks(self, quick=False, platform="darwin", config=None):
        return doctor.run_checks(self.ws, config or Config(), self.runner, quick=quick, platform=platform,
                                 now=self.now, home=str(self.home))

    def by_name(self, **kwargs):
        return {check.name: check for check in self.checks(**kwargs)}


@pytest.fixture
def mac(tmp_path, at, monkeypatch):
    monkeypatch.setattr(doctor, "disk_usage", lambda path: Usage(500 * GB, 400 * GB, 100 * GB))
    monkeypatch.setattr(keychain.getpass, "getuser", lambda: "someone")
    monkeypatch.setitem(sys.modules, "brainkit.slack_api", None)  # no Slack network check by default
    return Mac(tmp_path, at)


def fake_slack_module(monkeypatch, answer=None, error=None):
    calls = []

    def auth_test(runner, token):
        calls.append(token)
        if error is not None:
            raise error
        return answer

    module = types.ModuleType("brainkit.slack_api")
    module.auth_test = auth_test
    monkeypatch.setitem(sys.modules, "brainkit.slack_api", module)
    return calls


# ---------------------------------------------------------------- overall


def test_healthy_mac_has_no_problems(mac):
    checks = mac.checks()
    problems = [c for c in checks if c.status in ("warn", "fail")]
    assert problems == []
    found = {c.name: c for c in checks}
    expected = ["macos", "cpu", "command line tools", "python", "kit", "folders", "settings", "claude",
                "claude login", "claude trust", "codex", "codex login", "codex safety rules", "google", "slack",
                "schedule", "last catch-up", "disk", "time machine", "mdm", "t3 app"]
    assert [c.name for c in checks] == expected
    assert found["macos"].message == "macOS 14.5"
    assert found["cpu"].message == "Apple silicon (arm64)" and found["cpu"].status == "info"
    assert found["mdm"].message == "MDM enrollment: No" and found["mdm"].status == "info"
    assert all(c.status in doctor.STATUSES for c in checks)


def test_not_a_mac_skips_mac_only_checks(mac):
    found = mac.by_name(platform="linux")
    assert found["macos"].status == "info" and "not a Mac" in found["macos"].message
    for name in ("command line tools", "slack", "schedule", "time machine", "mdm", "t3 app"):
        assert name not in found


@pytest.mark.parametrize("version, status", [("12.7.1", "fail"), ("13.0", "ok"), ("15.4", "ok"), ("27.0", "ok"),
                                             ("garbage", "warn")])
def test_macos_version(mac, version, status):
    override(mac.runner, "sw_vers", result=Result(0, version + "\n"))
    assert mac.by_name()["macos"].status == status


def test_command_line_tools_missing_fails(mac):
    override(mac.runner, "xcode-select", result=Result(2, "", "unable to get active developer directory"))
    check = mac.by_name()["command line tools"]
    assert check.status == "fail" and check.fix == "run `xcode-select --install`"


def test_crashing_check_becomes_a_warning(mac, monkeypatch):
    def broken(ctx):
        raise RuntimeError("kaboom " + TOKEN)

    monkeypatch.setattr(doctor, "check_disk", broken)
    found = mac.by_name()
    assert found["disk"].status == "warn" and "kaboom" in found["disk"].message
    assert TOKEN not in found["disk"].message
    assert "t3 app" in found  # later checks still ran


# ---------------------------------------------------------------- Claude


def test_claude_missing_fails_and_skips_login_checks(mac):
    override(mac.runner, "claude", result=Result(127, "", "command not found: claude"))
    found = mac.by_name()
    assert found["claude"].status == "fail"
    assert "claude login" not in found and "claude trust" not in found


def test_claude_found_in_local_bin_when_not_on_path(mac):
    local = mac.home / ".local" / "bin" / "claude"
    local.parent.mkdir(parents=True)
    local.write_text("#!/bin/sh\n")
    override(mac.runner, "claude", result=Result(127))
    override(mac.runner, str(local), "--version", result=Result(0, "2.1.300 (Claude Code)\n"))
    override(mac.runner, str(local), "auth", "status", result=Result(0, '{"loggedIn": true}'))
    found = mac.by_name()
    assert found["claude"].status == "ok" and found["claude login"].status == "ok"
    assert [str(local), "auth", "status"] in mac.runner.argvs()


def test_claude_logged_out_fails_with_terminal_fix(mac):
    override(mac.runner, "claude", "auth", "status", result=Result(1, '{"loggedIn": false}'))
    check = mac.by_name()["claude login"]
    assert check.status == "fail"
    assert check.fix == ("open Terminal, run `cd ~/brain && claude`, log in with your work account, "
                         "then type /exit")


def test_claude_login_json_false_counts_as_logged_out(mac):
    override(mac.runner, "claude", "auth", "status", result=Result(0, '{"loggedIn": false}'))
    assert mac.by_name()["claude login"].status == "fail"


def test_claude_login_unknown_outcome_warns(mac):
    override(mac.runner, "claude", "auth", "status", result=Result(124, "", "timed out"))
    check = mac.by_name()["claude login"]
    assert check.status == "warn" and check.message == "couldn't confirm Claude login"
    assert doctor.format_line(check).startswith(
        "! claude login: couldn't confirm Claude login — open Terminal, run `cd ~/brain && claude`")


def test_check_claude_login_is_one_isolated_call():
    fake = FakeRunner().on("claude", "auth", "status", result=Result(0, "{}"))
    assert doctor.check_claude_login(fake).status == "ok"
    assert fake.argvs() == [["claude", "auth", "status"]]


def test_claude_trust_states(mac):
    assert mac.by_name()["claude trust"].status == "ok"
    mac.trust(False)
    check = mac.by_name()["claude trust"]
    assert check.status == "warn"
    assert check.message == "Claude hasn't been told to trust the brain folder yet"
    assert check.fix == "run `cd ~/brain && claude`, accept the question about trusting this folder, then /exit"
    (mac.home / ".claude.json").write_text("{not json")
    assert mac.by_name()["claude trust"].message.startswith("couldn't read")
    (mac.home / ".claude.json").unlink()
    assert mac.by_name()["claude trust"].status == "warn"


def test_claude_trust_never_writes_the_file(mac):
    path = mac.home / ".claude.json"
    before = (path.read_bytes(), os.stat(path).st_mtime_ns)
    mac.checks()
    assert (path.read_bytes(), os.stat(path).st_mtime_ns) == before


# ---------------------------------------------------------------- Codex


def test_codex_is_optional(mac):
    override(mac.runner, "codex", result=Result(127))
    found = mac.by_name()
    assert found["codex"].status == "info" and "optional" in found["codex"].message
    assert "codex login" not in found and "codex safety rules" not in found


def test_codex_logged_out_is_info_and_missing_rules_warn(mac):
    override(mac.runner, "codex", "login", "status", result=Result(1, "Not logged in\n"))
    (mac.home / ".codex" / "rules" / "brain-starter.rules").unlink()
    found = mac.by_name()
    assert found["codex login"].status == "info"
    assert found["codex safety rules"].status == "warn"
    assert found["codex safety rules"].fix == "run `brain repair`"


def test_codex_login_output_is_never_shown(mac):
    override(mac.runner, "codex", "login", "status", result=Result(0, "Logged in using an API key - sk-proj-***abcd\n"))
    assert "sk-" not in mac.by_name()["codex login"].message


# ---------------------------------------------------------------- Google


@pytest.mark.parametrize("change, message_part", [
    ({"has_refresh_token": False}, "isn't connected"),
    ({"token_valid": False}, "expired"),
    ({"scopes": [SCOPE + "gmail.readonly", SCOPE + "gmail.compose", SCOPE + "calendar.readonly"]}, "drive.readonly"),
    ({"scopes": []}, "gmail.readonly, gmail.compose, calendar.readonly, drive.readonly"),
])
def test_google_problems_fail_with_connect_fix(mac, change, message_part):
    status = dict(GOOD_GWS, **change)
    override(mac.runner, "gws", "auth", "status", result=Result(1, json.dumps(status)))
    check = mac.by_name()["google"]
    assert check.status == "fail" and message_part in check.message
    assert check.fix == "run `brain google connect`"
    assert "someone@example.com" not in check.message


def test_google_broader_scopes_are_enough(mac):
    broad = dict(GOOD_GWS, scopes=[SCOPE + "gmail.modify", SCOPE + "calendar", SCOPE + "drive"])
    override(mac.runner, "gws", "auth", "status", result=Result(0, json.dumps(broad)))
    assert mac.by_name()["google"].status == "ok"
    full_mail = dict(GOOD_GWS, scopes=["https://mail.google.com/", SCOPE + "calendar.readonly", SCOPE + "drive"])
    override(mac.runner, "gws", "auth", "status", result=Result(0, json.dumps(full_mail)))
    assert mac.by_name()["google"].status == "ok"


def test_google_tool_missing_or_unreadable(mac):
    override(mac.runner, "gws", "auth", "status", result=Result(1, "not json"))
    assert mac.by_name()["google"].message == "couldn't read the Google connection status"
    override(mac.runner, "gws", result=Result(127))
    check = mac.by_name()["google"]
    assert check.status == "fail" and check.fix == "run `brain tools install`"


# ---------------------------------------------------------------- Slack


def test_slack_missing_warns(mac):
    override(mac.runner, "security", result=Result(44))
    check = mac.by_name()["slack"]
    assert check.status == "warn" and check.fix == "run `brain secret set slack --gui`"


def test_slack_auth_test_reports_ids_only(mac, monkeypatch):
    calls = fake_slack_module(monkeypatch, answer={"ok": True, "team_id": "T0001", "user_id": "U0002",
                                                   "team": "Example Co", "user": "someone"})
    checks = mac.checks()
    slack = {c.name: c for c in checks}["slack"]
    assert slack.status == "ok" and slack.message == "connected (team T0001, user U0002)"
    assert calls == [TOKEN]
    assert TOKEN not in json.dumps([c.to_dict() for c in checks])


def test_slack_auth_error_is_scrubbed(mac, monkeypatch):
    fake_slack_module(monkeypatch, error=RuntimeError(f"bad request with {TOKEN}"))
    check = mac.by_name()["slack"]
    assert check.status == "warn" and TOKEN not in check.message and "<hidden>" in check.message


def test_slack_rejected_token_warns(mac, monkeypatch):
    fake_slack_module(monkeypatch, answer={"ok": False, "error": "invalid_auth"})
    check = mac.by_name()["slack"]
    assert check.status == "warn" and "invalid_auth" in check.message


def test_quick_skips_slack_network_and_last_catchup(mac, monkeypatch):
    calls = fake_slack_module(monkeypatch, answer={"ok": True})
    found = mac.by_name(quick=True)
    assert found["slack"].message == "token saved" and calls == []
    assert "last catch-up" not in found


# ---------------------------------------------------------------- schedule and machine


def test_schedule_not_loaded_warns(mac):
    override(mac.runner, "launchctl", "print", result=Result(113, "", "Could not find service"))
    check = mac.by_name()["schedule"]
    assert check.status == "warn" and check.fix == "run `brain schedule install`"


def test_schedule_failed_last_run_warns(mac):
    override(mac.runner, "launchctl", "print", result=Result(0, "\tstate = not running\n\tlast exit code = 1\n"))
    assert mac.by_name()["schedule"].status == "warn"


def test_last_catchup_rules(mac, at):
    mac.ws.runs_file.unlink()
    assert mac.by_name()["last catch-up"].status == "warn"  # Tuesday, never ran
    mac.now = at(2026, 10, 17, 10, 0)  # Saturday
    assert mac.by_name()["last catch-up"].status == "info"
    RunsLedger(mac.ws).record("catchup", "error", at(2026, 10, 15, 9, 0), at(2026, 10, 15, 9, 1))
    mac.now = at(2026, 10, 16, 12, 0)  # Friday, 26 h 59 min later
    check = mac.by_name()["last catch-up"]
    assert check.status == "warn" and "26 h" in check.message
    mac.now = at(2026, 10, 16, 8, 0)  # 22 h 59 min later: fine
    assert mac.by_name()["last catch-up"].status == "ok"


def test_low_disk_warns(mac, monkeypatch):
    monkeypatch.setattr(doctor, "disk_usage", lambda path: Usage(500 * GB, 499 * GB, 1 * GB))
    check = mac.by_name()["disk"]
    assert check.status == "warn" and "1.0 GB" in check.message


def test_time_machine_states(mac):
    override(mac.runner, "tmutil", result=Result(0, "No destinations configured.\n"))
    assert mac.by_name()["time machine"].status == "warn"
    override(mac.runner, "tmutil", result=Result(1, "", "tmutil: requires Full Disk Access"))
    assert mac.by_name()["time machine"].status == "info"


def test_mdm_enrolled_is_info_only(mac):
    override(mac.runner, "profiles", result=Result(0, "Enrolled via DEP: Yes\nMDM enrollment: Yes (User Approved)\n"))
    check = mac.by_name()["mdm"]
    assert check.status == "info" and check.message == "MDM enrollment: Yes (User Approved)"


def test_t3_app(mac):
    assert doctor.T3_APP_PATHS == ["/Applications/T3 Code (Alpha).app", "~/Applications/T3 Code (Alpha).app"]
    assert mac.by_name()["t3 app"].status == "ok"
    (mac.home / "Applications" / "T3 Code (Alpha).app").rmdir()
    if not os.path.exists("/Applications/T3 Code (Alpha).app"):
        assert mac.by_name()["t3 app"].status == "info"


# ---------------------------------------------------------------- kit, folders, settings


def test_kit_update_available_is_info(mac, at):
    RunsLedger(mac.ws).record("update_check", "ok", at(2026, 10, 20, 6, 0), at(2026, 10, 20, 6, 1),
                              data={"current": "0.1.0", "latest": "0.2.0", "update_available": True,
                                    "notes": "Faster briefs\nMore detail"})
    check = mac.by_name()["kit"]
    assert check.status == "info" and "0.2.0" in check.message and "Faster briefs" in check.message
    assert check.fix == "run `brain update`"


def test_missing_folders(mac, tmp_path):
    mac.ws.tasks.rmdir()
    check = mac.by_name()["folders"]
    assert check.status == "warn" and "tasks" in check.message
    mac.ws = Workspace(tmp_path / "nowhere")
    assert mac.by_name()["folders"].status == "fail"


def test_bad_settings_warn(mac):
    check = mac.by_name(config=Config(brief_time="half seven"))["settings"]
    assert check.status == "warn" and "brief_time" in check.message


# ---------------------------------------------------------------- CLI


@pytest.fixture
def cli_mac(mac, monkeypatch):
    monkeypatch.setattr(doctor, "_runner", lambda: mac.runner)
    monkeypatch.setattr(doctor, "_platform", lambda: "darwin")
    monkeypatch.setenv("HOME", str(mac.home))
    return mac


def test_cli_json_output(cli_mac, capsys):
    assert cli.main(["--workspace", str(cli_mac.ws.root), "doctor", "--json", "--quick"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert isinstance(data, list) and {"name", "status", "message", "fix"} <= set(data[0])
    assert TOKEN not in json.dumps(data)


def test_cli_exits_one_on_any_fail(cli_mac, capsys):
    override(cli_mac.runner, "claude", result=Result(127))
    assert cli.main(["--workspace", str(cli_mac.ws.root), "doctor"]) == 1
    out = capsys.readouterr().out
    assert "✗ claude: Claude Code isn't installed" in out
    assert "✓ macos: macOS 14.5" in out and "· cpu: Apple silicon (arm64)" in out


def test_human_line_format():
    assert doctor.format_line(doctor.Check("slack", "warn", "Slack isn't connected", "run `x`")) == \
        "! slack: Slack isn't connected — run `x`"
    assert doctor.format_line(doctor.Check("disk", "ok", "90 GB free", "ignored")) == "✓ disk: 90 GB free"


def test_claude_login_check_uses_the_saved_token_like_the_engine(ws, tmp_path):
    """Review finding 12: with the claude-oauth-token fallback, `claude auth status` must see the
    token too, or every brief opens with a false 'not logged in'."""
    from datetime import datetime, timezone

    from brainkit import doctor
    from brainkit.config import Config
    from brainkit.runner import FakeRunner, Result

    fake = FakeRunner()
    fake.on("security", "find-generic-password", "-s", "brain.claude-oauth-token",
            result=Result(0, "sk-ant-oat01-EXAMPLEEXAMPLE\n"))
    ctx = doctor.Context(ws=ws, config=Config(), runner=fake, quick=True, platform="darwin",
                         env={"PATH": "/usr/bin"}, now=datetime(2026, 10, 13, tzinfo=timezone.utc),
                         home=str(tmp_path))
    env = doctor._engine_env(ctx)
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "sk-ant-oat01-EXAMPLEEXAMPLE"
    assert env["PATH"] == "/usr/bin"
    no_token = FakeRunner().on("security", result=Result(44, ""))
    ctx2 = doctor.Context(ws=ws, config=Config(), runner=no_token, quick=True, platform="darwin",
                          env={"PATH": "/usr/bin"}, now=datetime(2026, 10, 13, tzinfo=timezone.utc),
                          home=str(tmp_path))
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in doctor._engine_env(ctx2)
