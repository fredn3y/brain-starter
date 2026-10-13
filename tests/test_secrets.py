"""Keychain secrets through a faked `security`: values never reach argv, output or logs."""

from __future__ import annotations

import pytest

from brainkit import cli
from brainkit import secrets as keychain
from brainkit.runner import FakeRunner, Result

SECRET = "xoxp-1234567890-abcdefghijkl-TOPSECRETVALUE"
USER = "someone"


def keychain_runner(stored=None):
    """A fake `security` that keeps one value in memory, like the real Keychain would."""
    state = {"value": stored}
    fake = FakeRunner()

    def interactive(argv):
        return Result(0)

    def find(argv):
        if state["value"] is None:
            return Result(44, "", "The specified item could not be found in the keychain.")
        if "-w" in argv:
            return Result(0, state["value"] + "\n")
        return Result(0, 'keychain: "login.keychain-db"\nclass: "genp"\n')

    def delete(argv):
        if state["value"] is None:
            return Result(44)
        state["value"] = None
        return Result(0)

    fake.on("security", "-i", result=interactive)
    fake.on("security", "find-generic-password", result=find)
    fake.on("security", "delete-generic-password", result=delete)
    fake.state = state
    return fake


def all_argv_text(fake):
    return " ".join(" ".join(argv) for argv in fake.argvs())


def test_get_returns_value_or_none(fake):
    fake.on("security", "find-generic-password", result=Result(0, SECRET + "\n"))
    assert keychain.get(fake, "slack", user=USER) == SECRET
    assert fake.argvs()[0] == ["security", "find-generic-password", "-s", "brain.slack", "-a", USER, "-w"]
    missing = FakeRunner().on("security", result=Result(44))
    assert keychain.get(missing, "slack", user=USER) is None


def test_get_raises_on_other_errors_without_value():
    locked = FakeRunner().on("security", result=Result(51, "", "User interaction is not allowed."))
    with pytest.raises(keychain.SecretError) as info:
        keychain.get(locked, "slack", user=USER)
    assert "exit 51" in str(info.value)


def test_exists_never_reads_the_value(fake):
    fake.on("security", "find-generic-password", result=Result(0, "attributes only"))
    assert keychain.exists(fake, "airwallex-key", user=USER) is True
    assert fake.argvs()[0] == ["security", "find-generic-password", "-s", "brain.airwallex-key", "-a", USER]
    assert "-w" not in fake.argvs()[0]
    assert keychain.exists(FakeRunner().on("security", result=Result(44)), "slack", user=USER) is False


def test_store_sends_the_value_on_stdin_only():
    fake = keychain_runner()

    def capture(argv):
        fake.state["value"] = SECRET  # pretend security stored it
        return Result(0)

    fake.rules.insert(0, (("security", "-i"), capture))
    keychain.store(fake, "slack", "  " + SECRET + "\n", user=USER)
    assert SECRET not in all_argv_text(fake)
    first = fake.calls[0]
    assert first["argv"] == ["security", "-i"]
    assert first["input"] == f"add-generic-password -U -s 'brain.slack' -a '{USER}' -w '{SECRET}'\n"


def test_store_detects_a_value_that_did_not_stick():
    fake = keychain_runner(stored="an-older-value-123")
    with pytest.raises(keychain.SecretError) as info:
        keychain.store(fake, "slack", SECRET, user=USER)
    assert SECRET not in str(info.value)


def test_store_failure_message_hides_value_and_output():
    fake = FakeRunner().on("security", "-i", result=Result(1, f"echo: {SECRET}", f"error near {SECRET}"))
    with pytest.raises(keychain.SecretError) as info:
        keychain.store(fake, "slack", SECRET, user=USER)
    assert SECRET not in str(info.value) and "exit 1" in str(info.value)


@pytest.mark.parametrize("bad", ["short", "has a space in it", "quote'inside-value", 'dq"inside-value',
                                 "back\\slash-value", "x" * 1025, "", "semi;colon-value"])
def test_store_rejects_bad_values_without_echoing_them(fake, bad):
    with pytest.raises(ValueError) as info:
        keychain.store(fake, "slack", bad, user=USER)
    if bad.strip():
        assert bad.strip() not in str(info.value)
    assert fake.calls == []


def test_long_claude_token_is_allowed():
    value = "sk-ant-oat01-" + "A" * 900
    assert keychain.validate("claude-oauth-token", value) == value
    assert keychain.service("claude-oauth-token") == "brain.claude-oauth-token"


def test_unknown_names_are_rejected(fake):
    with pytest.raises(keychain.SecretError):
        keychain.get(fake, "github", user=USER)
    assert fake.calls == []


def test_unusual_account_names_are_rejected(fake):
    with pytest.raises(keychain.SecretError):
        keychain.exists(fake, "slack", user="bad' -w 'x")


def test_delete(fake):
    fake.on("security", "delete-generic-password", result=Result(0))
    assert keychain.delete(fake, "slack", user=USER) is True
    assert fake.argvs()[0] == ["security", "delete-generic-password", "-s", "brain.slack", "-a", USER]
    assert keychain.delete(FakeRunner().on("security", result=Result(44)), "slack", user=USER) is False


def test_prompt_value_gui_uses_a_hidden_dialog():
    fake = FakeRunner().on("osascript", result=Result(0, SECRET + "\n"))
    assert keychain.prompt_value(fake, "slack", gui=True) == SECRET
    argv = fake.argvs()[0]
    assert argv[:2] == ["osascript", "-e"]
    assert "with hidden answer" in argv[2] and 'buttons {"Cancel", "Save"}' in argv[2]
    assert "Paste your Slack token here. It is saved in your Mac Keychain." in argv[2]
    assert SECRET not in " ".join(argv)


def test_prompt_value_gui_cancel():
    fake = FakeRunner().on("osascript", result=Result(1, "", "execution error: User canceled. (-128)"))
    assert keychain.prompt_value(fake, "slack", gui=True) is None


def test_prompt_value_terminal():
    asked = []
    value = keychain.prompt_value(FakeRunner(), "slack", gui=False,
                                  ask=lambda prompt: asked.append(prompt) or f"  {SECRET}  ")
    assert value == SECRET and "Slack token" in asked[0]
    assert keychain.prompt_value(FakeRunner(), "slack", gui=False, ask=lambda p: "   ") is None


# ---------------------------------------------------------------- CLI


@pytest.fixture
def mac_cli(monkeypatch):
    fake = keychain_runner()
    monkeypatch.setattr(keychain, "_runner", lambda: fake)
    monkeypatch.setattr(keychain, "_platform", lambda: "darwin")
    monkeypatch.setattr(keychain.getpass, "getuser", lambda: USER)
    return fake


def test_cli_set_gui_never_shows_the_value(ws, mac_cli, capsys):
    def dialog(argv):
        return Result(0, SECRET + "\n")

    def store(argv):
        mac_cli.state["value"] = SECRET
        return Result(0)

    mac_cli.rules.insert(0, (("osascript",), dialog))
    mac_cli.rules.insert(0, (("security", "-i"), store))
    assert cli.main(["--workspace", str(ws.root), "secret", "set", "slack", "--gui"]) == 0
    out, err = capsys.readouterr()
    assert "Saved: slack" in out
    assert SECRET not in out + err
    assert SECRET not in all_argv_text(mac_cli)
    log_text = (ws.logs / "brain.log").read_text()
    assert "secret set slack: saved" in log_text and SECRET not in log_text


def test_cli_set_rejects_bad_value_without_echo(ws, mac_cli, capsys):
    mac_cli.rules.insert(0, (("osascript",), Result(0, "not valid at all\n")))
    assert cli.main(["--workspace", str(ws.root), "secret", "set", "slack", "--gui"]) == 1
    out, err = capsys.readouterr()
    assert "not valid at all" not in out + err and "Slack token" in err


def test_cli_set_cancelled(ws, mac_cli, capsys):
    mac_cli.rules.insert(0, (("osascript",), Result(1)))
    assert cli.main(["--workspace", str(ws.root), "secret", "set", "slack", "--gui"]) == 1
    assert "Nothing saved" in capsys.readouterr().out


def test_cli_set_needs_a_terminal_or_gui(ws, mac_cli, monkeypatch, capsys):
    monkeypatch.setattr(keychain, "_stdin_is_terminal", lambda: False)
    assert cli.main(["--workspace", str(ws.root), "secret", "set", "slack"]) == 2
    assert "--gui" in capsys.readouterr().err
    assert mac_cli.calls == []


def test_cli_set_in_terminal_uses_hidden_prompt(ws, mac_cli, monkeypatch, capsys):
    def store(argv):
        mac_cli.state["value"] = SECRET
        return Result(0)

    mac_cli.rules.insert(0, (("security", "-i"), store))
    monkeypatch.setattr(keychain, "_stdin_is_terminal", lambda: True)
    monkeypatch.setattr(keychain, "_ask", lambda prompt: SECRET)
    assert cli.main(["--workspace", str(ws.root), "secret", "set", "slack"]) == 0
    out, err = capsys.readouterr()
    assert SECRET not in out + err


def test_cli_check_list_delete_print_names_only(ws, mac_cli, capsys):
    mac_cli.state["value"] = SECRET
    assert cli.main(["--workspace", str(ws.root), "secret", "check", "slack"]) == 0
    assert cli.main(["--workspace", str(ws.root), "secret", "list"]) == 0
    assert cli.main(["--workspace", str(ws.root), "secret", "delete", "slack"]) == 0
    assert cli.main(["--workspace", str(ws.root), "secret", "check", "slack"]) == 1
    out, err = capsys.readouterr()
    assert "slack: present" in out and "Deleted: slack" in out and "slack: missing" in out
    assert "airwallex-key: present" in out  # the fake keychain answers for every name
    assert SECRET not in out + err
    assert all("-w" not in argv for argv in mac_cli.argvs())  # check/list never read values


def test_cli_refuses_off_mac(ws, monkeypatch, capsys):
    monkeypatch.setattr(keychain, "_platform", lambda: "linux")
    assert cli.main(["--workspace", str(ws.root), "secret", "list"]) == 2


def test_cli_unknown_name_is_a_usage_error(ws, mac_cli):
    with pytest.raises(SystemExit):
        cli.main(["--workspace", str(ws.root), "secret", "check", "github"])
