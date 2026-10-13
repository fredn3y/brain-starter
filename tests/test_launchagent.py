"""LaunchAgent plist and launchctl calls (all faked; nothing touches the real ~/Library)."""

from __future__ import annotations

import plistlib
import sys

from brainkit import cli, launchagent
from brainkit.config import Config
from brainkit.runner import FakeRunner, Result

HOME = "/Users/someone"
LABEL = "local.brain-starter.catchup"
PRINT_OUTPUT = """gui/501/local.brain-starter.catchup = {
\tactive count = 0
\tpath = /Users/someone/Library/LaunchAgents/local.brain-starter.catchup.plist
\ttype = LaunchAgent
\tstate = not running

\tprogram = /usr/bin/caffeinate
\tlast exit code = 0
\tevent triggers = {
\t\tstate = active
\t}
}
"""


def plist_for(ws, **overrides):
    config = overrides.pop("config", Config())
    return launchagent.build_plist(ws, "/usr/bin/python3", config, HOME, user="someone")


def test_plist_matches_the_contract(ws):
    data = plist_for(ws)
    root = str(ws.root)
    assert data["Label"] == LABEL
    assert data["ProgramArguments"] == ["/usr/bin/caffeinate", "-i", "-s", "/usr/bin/python3",
                                        f"{root}/.kit/bin/brain", "catchup"]
    assert data["StartCalendarInterval"] == [{"Hour": 7, "Minute": 30}, {"Minute": 0}]
    assert data["RunAtLoad"] is True
    assert data["WorkingDirectory"] == root
    assert data["EnvironmentVariables"] == {
        "PATH": f"{root}/.kit/bin:{HOME}/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": HOME,
        "USER": "someone",
        "BRAIN_HOME": root,
    }
    assert data["StandardOutPath"] == data["StandardErrorPath"] == f"{root}/logs/launchd.log"
    assert "~" not in data["EnvironmentVariables"]["PATH"]


def test_plist_uses_configured_brief_time_and_survives_bad_values(ws):
    assert plist_for(ws, config=Config(brief_time="06:05"))["StartCalendarInterval"][0] == {"Hour": 6, "Minute": 5}
    assert plist_for(ws, config=Config(brief_time="soon"))["StartCalendarInterval"][0] == {"Hour": 7, "Minute": 30}


def test_plist_round_trips_through_plistlib(ws):
    data = plist_for(ws)
    assert plistlib.loads(plistlib.dumps(data)) == data


def test_relative_workspace_becomes_absolute(tmp_path, monkeypatch):
    from brainkit.paths import Workspace

    monkeypatch.chdir(tmp_path)
    data = launchagent.build_plist(Workspace("brain"), "/usr/bin/python3", Config(), HOME, user="someone")
    assert data["WorkingDirectory"] == str(tmp_path / "brain")


def test_python_path_prefers_apple_python():
    assert launchagent.python_path(exists=lambda path: True) == "/usr/bin/python3"
    assert launchagent.python_path(exists=lambda path: False, fallback="/opt/py") == "/opt/py"
    assert launchagent.python_path(exists=lambda path: False) == sys.executable


def test_job_env_always_has_path_home_user_brain_home(ws):
    env = launchagent.job_env(ws, HOME, base={"TMPDIR": "/tmp/x", "SECRET_TOKEN": "nope"}, user="someone")
    assert env["PATH"].startswith(f"{ws.kit_bin}:{HOME}/.local/bin:")
    assert env["HOME"] == HOME and env["USER"] == "someone" and env["BRAIN_HOME"] == str(ws.root)
    assert env["TMPDIR"] == "/tmp/x" and "SECRET_TOKEN" not in env


def test_missing_user_name_never_crashes(ws, monkeypatch):
    def nobody():
        raise OSError("no user name")

    monkeypatch.setattr(launchagent.getpass, "getuser", nobody)
    assert "USER" not in launchagent.job_env(ws, HOME, base={})
    assert "USER" not in launchagent.build_plist(ws, "/usr/bin/python3", Config(), HOME)["EnvironmentVariables"]


def test_install_writes_plist_then_bootout_and_bootstrap(ws, tmp_path):
    agents = tmp_path / "LaunchAgents"
    fake = FakeRunner().on("launchctl", "bootout", result=Result(3, "", "No such process"))
    fake.on("launchctl", "bootstrap", result=Result(0))
    outcome = launchagent.install(ws, fake, Config(), agents_dir=agents, uid=501, home=HOME,
                                  python="/usr/bin/python3", user="someone", sleep=lambda s: None)
    target = agents / f"{LABEL}.plist"
    assert outcome.ok and target.exists()
    with open(target, "rb") as handle:
        assert plistlib.load(handle) == plist_for(ws)
    assert fake.argvs() == [["launchctl", "bootout", f"gui/501/{LABEL}"],
                            ["launchctl", "bootstrap", "gui/501", str(target)]]
    assert (ws.logs).is_dir()


def test_install_retries_bootstrap_once_bootout_settles(ws, tmp_path):
    answers = iter([Result(5, "", "Bootstrap failed: 5: Input/output error"), Result(0)])
    fake = FakeRunner().on("launchctl", "bootout", result=Result(0))
    fake.on("launchctl", "bootstrap", result=lambda argv: next(answers))
    sleeps = []
    outcome = launchagent.install(ws, fake, Config(), agents_dir=tmp_path, uid=501, home=HOME,
                                  python="/usr/bin/python3", user="someone", sleep=sleeps.append)
    assert outcome.ok and sleeps == [1.0]
    assert [argv[1] for argv in fake.argvs()] == ["bootout", "bootstrap", "bootstrap"]


def test_install_reports_failure(ws, tmp_path):
    fake = FakeRunner().on("launchctl", "bootout", result=Result(0))
    fake.on("launchctl", "bootstrap", result=Result(5, "", "Bootstrap failed: 5: Input/output error"))
    outcome = launchagent.install(ws, fake, Config(), agents_dir=tmp_path, uid=501, home=HOME,
                                  python="/usr/bin/python3", user="someone", sleep=lambda s: None)
    assert not outcome.ok and "exit 5" in outcome.message
    assert len(fake.argvs()) == 1 + launchagent.BOOTSTRAP_TRIES


def test_uninstall_boots_out_and_removes_plist(ws, tmp_path):
    target = tmp_path / f"{LABEL}.plist"
    target.write_text("x")
    fake = FakeRunner().on("launchctl", "bootout", result=Result(0))
    assert launchagent.uninstall(ws, fake, agents_dir=tmp_path, uid=501).ok
    assert not target.exists()
    assert fake.argvs() == [["launchctl", "bootout", f"gui/501/{LABEL}"]]
    assert launchagent.uninstall(ws, fake, agents_dir=tmp_path, uid=501).ok  # already gone: fine


def test_status_parses_launchctl_print(ws, tmp_path):
    fake = FakeRunner().on("launchctl", "print", result=Result(0, PRINT_OUTPUT))
    info = launchagent.status(ws, fake, agents_dir=tmp_path, uid=501)
    assert info["loaded"] is True and info["state"] == "not running" and info["last_exit_code"] == "0"
    assert fake.argvs() == [["launchctl", "print", f"gui/501/{LABEL}"]]
    assert info["plist_exists"] is False and info["points_here"] is None


def test_status_not_loaded(ws, tmp_path):
    fake = FakeRunner().on("launchctl", "print", result=Result(113, "", "Could not find service"))
    info = launchagent.status(ws, fake, agents_dir=tmp_path, uid=501)
    assert info["loaded"] is False and info["state"] is None


def test_status_knows_whether_the_plist_points_at_this_workspace(ws, tmp_path):
    fake = FakeRunner().on("launchctl", "bootout", result=Result(0)).on("launchctl", "bootstrap", result=Result(0))
    launchagent.install(ws, fake, Config(), agents_dir=tmp_path, uid=501, home=HOME, python="/usr/bin/python3",
                        user="someone", sleep=lambda s: None)
    fake.on("launchctl", "print", result=Result(0, PRINT_OUTPUT))
    assert launchagent.status(ws, fake, agents_dir=tmp_path, uid=501)["points_here"] is True
    from brainkit.paths import Workspace

    other = Workspace(tmp_path / "elsewhere")
    assert launchagent.status(other, fake, agents_dir=tmp_path, uid=501)["points_here"] is False


def test_schedule_cli_refuses_off_mac(ws, monkeypatch, capsys):
    monkeypatch.setattr(launchagent, "_platform", lambda: "linux")
    assert cli.main(["--workspace", str(ws.root), "schedule", "status"]) == 2


def test_schedule_status_cli(ws, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))  # keep away from the real ~/Library
    fake = FakeRunner().on("launchctl", "print", result=Result(0, PRINT_OUTPUT))
    monkeypatch.setattr(launchagent, "_platform", lambda: "darwin")
    monkeypatch.setattr(launchagent, "_runner", lambda: fake)
    assert cli.main(["--workspace", str(ws.root), "schedule", "status"]) == 0
    out = capsys.readouterr().out
    assert "Loaded: yes" in out and "State: not running" in out


def test_schedule_without_action_prints_help(ws, capsys):
    assert cli.main(["--workspace", str(ws.root), "schedule"]) == 2


def test_plist_carries_a_custom_gws_location(ws, tmp_path, monkeypatch):
    from brainkit import launchagent
    from brainkit.config import Config
    monkeypatch.setenv("BRAIN_GWS_REAL", "/opt/tools/gws")
    plist = launchagent.build_plist(ws, "/usr/bin/python3", Config(), tmp_path, user="u")
    assert plist["EnvironmentVariables"]["BRAIN_GWS_REAL"] == "/opt/tools/gws"
    monkeypatch.delenv("BRAIN_GWS_REAL")
    plist = launchagent.build_plist(ws, "/usr/bin/python3", Config(), tmp_path, user="u")
    assert "BRAIN_GWS_REAL" not in plist["EnvironmentVariables"]
