"""gws.py (guard, JSON runs, auth status, drafts) and google_cli.py (connect, status, draft CLI)."""

from __future__ import annotations

import base64
import email
import json
import os
import stat
from datetime import datetime
from email import policy as email_policy
from pathlib import Path

import pytest

from brainkit import browser, google_cli, gws
from brainkit.cli import build_parser
from brainkit.runner import FakeRunner, Result

NOISE = "Using keyring backend: keyring\n"
SECRET = "GOCSPX-not-a-real-secret-value"


def decode_raw(raw: str):
    data = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
    return email.message_from_bytes(data, policy=email_policy.default)


def json_arg(argv, flag):
    return json.loads(argv[argv.index(flag) + 1])


def client_json(**overrides) -> str:
    installed = {"client_id": "123-abc.apps.googleusercontent.com", "client_secret": SECRET,
                 "project_id": "brain-project", "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                 "token_uri": "https://oauth2.googleapis.com/token", "redirect_uris": ["http://localhost"]}
    installed.update(overrides)
    return json.dumps({"installed": installed})


def status_json(scopes=None, valid=True) -> str:
    return json.dumps({"user": "someone@example.com", "token_valid": valid, "has_refresh_token": True,
                       "scopes": list(gws.REQUIRED_SCOPES) + ["openid"] if scopes is None else scopes,
                       "client_config": "/x/client_secret.json", "storage": "encrypted",
                       "keyring_backend": "keyring"})


# ---------------------------------------------------------------- running gws


def test_run_json_refuses_blocked_commands_without_running_anything(fake):
    with pytest.raises(gws.GwsBlocked) as info:
        gws.run_json(fake, ["gmail", "+send", "--to", "a@example.com"])
    assert str(info.value) == gws.BLOCKED_MESSAGE
    assert fake.calls == []


def test_run_json_parses_output_and_passes_env(fake):
    fake.on("gws", "calendar", result=Result(0, '{"count": 0, "events": []}\n', NOISE))
    env = {"PATH": "/brain/.kit/bin:/usr/bin", "HOME": "/home/x", "USER": "x"}
    assert gws.run_json(fake, ["calendar", "+agenda", "--today"], env=env) == {"count": 0, "events": []}
    assert fake.calls[0]["argv"][:3] == ["gws", "calendar", "+agenda"] and fake.calls[0]["env"] == env


def test_run_json_tolerates_a_log_line_before_the_json(fake):
    fake.on("gws", result=Result(0, 'note: something\n{"ok": 1}\n'))
    assert gws.run_json(fake, ["auth", "status"]) == {"ok": 1}


def test_failure_message_is_short_and_names_only_the_command(fake):
    error = {"error": {"code": 400, "message": "Invalid value " + "x" * 5000, "reason": "badRequest"}}
    fake.on("gws", result=Result(1, json.dumps(error), NOISE + "error[api]: boom\n"))
    with pytest.raises(gws.GwsError) as info:
        gws.run_json(fake, ["gmail", "+read", "--id", "PRIVATE-ID-123", "--headers"])
    message = str(info.value)
    assert message.startswith("gws gmail +read failed: Invalid value")
    assert len(message) < 260 and "PRIVATE-ID-123" not in message


def test_gws_validation_exit_3_is_not_mistaken_for_the_guard(fake):
    fake.on("gws", result=Result(3, "", NOISE + "error[validation]: bad flag --frob\n"))
    with pytest.raises(gws.GwsError) as info:
        gws.run_json(fake, ["calendar", "+agenda", "--frob"])
    assert not isinstance(info.value, gws.GwsBlocked)
    assert str(info.value) == "gws calendar +agenda failed: error[validation]: bad flag --frob"


def test_the_shims_refusal_becomes_gws_blocked(fake):
    fake.on("gws", result=Result(3, "", gws.BLOCKED_MESSAGE + "\n"))
    with pytest.raises(gws.GwsBlocked):
        gws.run_json(fake, ["gmail", "users", "threads", "list"])


def test_missing_gws_and_sign_in_hints(fake):
    fake.on("gws", "auth", result=Result(127, "", "command not found: gws"))
    with pytest.raises(gws.GwsError, match="brain tools install gws"):
        gws.run_json(fake, ["auth", "status"])
    expired = {"error": {"code": 401, "message": "Token has been expired or revoked.", "reason": "authError"}}
    fake.on("gws", "gmail", result=Result(2, json.dumps(expired), NOISE))
    with pytest.raises(gws.GwsError) as info:
        gws.run_json(fake, ["gmail", "+triage"])
    assert str(info.value).endswith("run `brain google connect`")


def test_auth_status_and_missing_scopes(fake):
    narrow = [gws.GMAIL_READONLY, gws.CALENDAR_READONLY]
    fake.on("gws", "auth", "status", result=Result(0, status_json(narrow), NOISE))
    status = gws.auth_status(fake)
    assert status["user"] == "someone@example.com"
    assert gws.missing_scopes(status) == [gws.GMAIL_COMPOSE, gws.DRIVE_READONLY]
    broad = {"scopes": [gws.SCOPE_BASE + "gmail.modify", gws.SCOPE_BASE + "calendar", gws.SCOPE_BASE + "drive"]}
    assert gws.missing_scopes(broad) == []
    assert gws.missing_scopes({"scopes": " ".join(gws.REQUIRED_SCOPES)}) == []
    assert gws.has_scope({"scopes": ["https://mail.google.com/"]}, gws.GMAIL_COMPOSE)


def test_auth_status_error_json_raises(fake):
    fake.on("gws", "auth", "status", result=Result(2, json.dumps({"error": {"code": 401, "message": "No credentials"}})))
    with pytest.raises(gws.GwsError, match="brain google connect"):
        gws.auth_status(fake)


# ---------------------------------------------------------------- drafts


def test_raw_message_round_trip():
    raw = gws.build_raw_message("Zoë Example <zoe@example.com>", "Café plans ☕", "Hello ünïcode\nSecond line\n",
                                cc="c@example.com", in_reply_to="<m1@mail>", references="<m0@mail> <m1@mail>")
    assert set(raw) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_=")
    msg = decode_raw(raw)
    assert msg["Subject"] == "Café plans ☕"
    assert msg["To"].addresses[0].addr_spec == "zoe@example.com"
    assert msg["To"].addresses[0].display_name == "Zoë Example"
    assert msg["Cc"] == "c@example.com"
    assert msg["In-Reply-To"] == "<m1@mail>" and msg["References"] == "<m0@mail> <m1@mail>"
    assert msg.get_content_charset() == "utf-8"
    assert msg.get_content().replace("\r\n", "\n") == "Hello ünïcode\nSecond line\n"


def test_raw_message_rejects_header_injection():
    with pytest.raises(ValueError):
        gws.build_raw_message("a@example.com\nBcc: other@example.com", "Hi", "Body")
    with pytest.raises(ValueError):
        gws.build_raw_message("a@example.com", "Hi\r\nBcc: other@example.com", "Body")


def test_create_draft_plain(fake):
    fake.on("gws", "gmail", "users", "drafts", "create",
            result=Result(0, json.dumps({"id": "r-1", "message": {"id": "m9", "threadId": "t9"}})))
    assert gws.create_draft(fake, "a@example.com", "Hi", "Body text") == {"id": "r-1", "threadId": "t9"}
    argv = fake.calls[0]["argv"]
    assert argv[:5] == ["gws", "gmail", "users", "drafts", "create"]
    assert json_arg(argv, "--params") == {"userId": "me"}
    message = json_arg(argv, "--json")["message"]
    assert "threadId" not in message
    parsed = decode_raw(message["raw"])
    assert parsed["Subject"] == "Hi" and parsed["To"] == "a@example.com" and parsed["In-Reply-To"] is None


def test_create_draft_reply_threads_under_the_original(fake):
    original = {"id": "m1", "threadId": "t1", "payload": {"headers": [
        {"name": "Message-Id", "value": "<orig@mail.example>"}, {"name": "Subject", "value": "Quarterly numbers"},
        {"name": "References", "value": "<older@mail.example>"}, {"name": "From", "value": "Pat <pat@example.com>"}]}}
    fake.on("gws", "gmail", "users", "messages", "get", result=Result(0, json.dumps(original)))
    fake.on("gws", "gmail", "users", "drafts", "create",
            result=Result(0, json.dumps({"id": "r-2", "message": {"threadId": "t1"}})))
    out = gws.create_draft(fake, "pat@example.com", "", "Thanks!", reply_to_message_id="m1")
    assert out == {"id": "r-2", "threadId": "t1"}
    params = json_arg(fake.calls[0]["argv"], "--params")
    assert params["id"] == "m1" and params["format"] == "metadata"
    assert {"Message-ID", "Subject", "References"} <= set(params["metadataHeaders"])
    message = json_arg(fake.calls[1]["argv"], "--json")["message"]
    assert message["threadId"] == "t1"
    parsed = decode_raw(message["raw"])
    assert parsed["Subject"] == "Re: Quarterly numbers"
    assert parsed["In-Reply-To"] == "<orig@mail.example>"
    assert parsed["References"] == "<older@mail.example> <orig@mail.example>"
    assert gws.reply_subject("Plans") == "Re: Plans" and gws.reply_subject("RE: Plans") == "RE: Plans"


# ---------------------------------------------------------------- connect


def write(path: Path, text: str, mtime: float) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    os.utime(str(path), (mtime, mtime))
    return path


def spawner(lines, code=0, calls=None):
    def spawn(argv, env, on_line, timeout):
        if calls is not None:
            calls.append({"argv": argv, "env": env, "timeout": timeout})
        for line in lines:
            on_line(line)
        return code
    return spawn


SIGN_IN = ("https://accounts.google.com/o/oauth2/auth?scope=openid&access_type=offline"
           "&redirect_uri=http://localhost:53682&response_type=code&client_id=123&state=xyz")


@pytest.fixture
def home(tmp_path) -> Path:
    path = tmp_path / "home"
    path.mkdir()
    return path


def test_connect_installs_the_newest_desktop_client_file_and_signs_in(ws, home, monkeypatch):
    monkeypatch.setattr(browser, "chrome_installed", lambda home=None, exists=None: False)
    downloads = home / "Downloads"
    write(downloads / "client_secret_old.json", client_json(client_id="old-id"), 1000)
    newest = write(downloads / "client_secret_123.json", client_json(), 2000)
    write(downloads / "client_secret_web.json", json.dumps({"web": {"client_id": "w"}}), 3000)
    fake = FakeRunner().on("open", result=Result(0)).on("gws", "auth", "status", result=Result(0, status_json()))
    out, calls = [], []
    lines = [NOISE, "Open this URL in your browser to authenticate:\n", "\n", f"  {SIGN_IN}\n",
             "Authentication successful. Encrypted credentials saved.\n"]
    env = {"HOME": str(home), "PATH": "/usr/bin", "USER": "someone"}
    code = google_cli.connect(ws, fake, env, home, spawn=spawner(lines, calls=calls), platform="darwin",
                              out=out.append, now=datetime(2026, 10, 21, 9, 12))
    assert code == 0
    dest = home / ".config" / "gws" / "client_secret.json"
    assert json.loads(dest.read_text())["installed"]["client_id"] == "123-abc.apps.googleusercontent.com"
    assert stat.S_IMODE(dest.stat().st_mode) == 0o600
    assert stat.S_IMODE(dest.parent.stat().st_mode) == 0o700
    assert not newest.exists()  # moved, not copied
    assert calls[0]["argv"] == ["gws", "auth", "login", "--scopes", ",".join(gws.REQUIRED_SCOPES)]
    assert calls[0]["env"] == env and calls[0]["timeout"] == google_cli.LOGIN_TIMEOUT
    assert fake.argvs().count(["open", SIGN_IN]) == 1
    text = "\n".join(out)
    assert SIGN_IN in text and "Authentication successful" in text
    assert "Google account: someone@example.com" in text and "Missing permissions: none" in text
    assert SECRET not in text and "keyring backend" not in text and "Open this URL" not in text


def test_sign_in_link_opens_in_chrome_when_installed(ws, home, tmp_path):
    (home / "Applications" / "Google Chrome.app").mkdir(parents=True)
    source = write(tmp_path / "client.json", client_json(), 1)
    fake = FakeRunner().on("open", result=Result(0)).on("gws", "auth", "status", result=Result(0, status_json()))
    code = google_cli.connect(ws, fake, {"HOME": str(home)}, home, client_file=str(source),
                              spawn=spawner([SIGN_IN + "\n"]), platform="darwin", out=[].append)
    assert code == 0 and ["open", "-a", "Google Chrome", SIGN_IN] in fake.argvs()


def test_open_url_prefers_chrome_and_falls_back(tmp_path, monkeypatch):
    url = "https://example.com/x?a=1"
    assert browser.open_url(FakeRunner(), url, platform="linux") is False
    monkeypatch.setattr(browser, "chrome_installed", lambda home=None, exists=None: False)
    plain = FakeRunner().on("open", result=Result(0))
    assert browser.open_url(plain, url, platform="darwin") and plain.argvs() == [["open", url]]
    monkeypatch.setattr(browser, "chrome_installed", lambda home=None, exists=None: True)
    broken = FakeRunner().on("open", "-a", result=Result(1, "", "Unable to find application")).on("open", result=Result(0))
    assert browser.open_url(broken, url, platform="darwin")
    assert broken.argvs() == [["open", "-a", "Google Chrome", url], ["open", url]]


def test_chrome_detection_looks_in_both_applications_folders(tmp_path):
    home = tmp_path / "home"
    (home / "Applications" / "Google Chrome.app").mkdir(parents=True)
    under_tmp = lambda path: path.startswith(str(tmp_path)) and os.path.isdir(path)  # noqa: E731
    assert browser.chrome_installed(home, exists=under_tmp)
    assert not browser.chrome_installed(tmp_path / "other", exists=under_tmp)
    assert browser.chrome_installed(tmp_path / "other", exists=lambda path: path == "/Applications/Google Chrome.app")


def test_connect_backs_up_a_different_existing_client_file(ws, home):
    config = home / ".config" / "gws"
    config.mkdir(parents=True)
    (config / "client_secret.json").write_text(client_json(client_id="previous"))
    write(ws.root / "inbox" / "client_secret_9.json", client_json(), 2000)
    fake = FakeRunner().on("gws", "auth", "status", result=Result(0, status_json()))
    out = []
    code = google_cli.connect(ws, fake, None, home, spawn=spawner([]), platform="linux", out=out.append,
                              now=datetime(2026, 10, 21, 9, 12, 5))
    assert code == 0
    backup = config / "client_secret.json.bak-20261021-091205"
    assert json.loads(backup.read_text())["installed"]["client_id"] == "previous"
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600
    assert "previous one is kept as client_secret.json.bak-20261021-091205" in "\n".join(out)
    assert fake.argvs() == [["gws", "auth", "status"]]  # no `open` outside macOS


def test_connect_without_downloads_access_looks_in_the_inbox(ws, home, monkeypatch):
    downloads = str(home / "Downloads")
    real_listdir = os.listdir

    def guarded(path):
        if str(path) == downloads:
            raise PermissionError(1, "Operation not permitted")
        return real_listdir(path)

    monkeypatch.setattr(google_cli.os, "listdir", guarded)
    out = []
    assert google_cli.connect(ws, FakeRunner(), None, home, spawn=spawner([]), out=out.append) == 1
    assert "not allowed to look in your Downloads folder" in out[0] and str(ws.root / "inbox") in out[0]
    assert (ws.root / "inbox").is_dir()
    write(ws.root / "inbox" / "client_secret_2.json", client_json(), 2000)
    fake = FakeRunner().on("gws", "auth", "status", result=Result(0, status_json()))
    assert google_cli.connect(ws, fake, None, home, spawn=spawner([]), platform="linux", out=[].append) == 0


def test_connect_explains_how_to_get_a_new_secret_when_nothing_is_found(ws, home):
    (home / "Downloads").mkdir()
    out = []
    assert google_cli.connect(ws, FakeRunner(), None, home, spawn=spawner([]), out=out.append) == 1
    assert "Add secret" in out[0] and "brain google connect" in out[0]


def test_connect_rejects_wrong_or_incomplete_client_files(ws, home, tmp_path):
    web = write(tmp_path / "web.json", json.dumps({"web": {"client_id": "w", "client_secret": SECRET}}), 1)
    out = []
    assert google_cli.connect(ws, FakeRunner(), None, home, client_file=str(web), out=out.append) == 1
    assert "Desktop app" in out[0]
    broken = write(tmp_path / "broken.json", client_json(client_secret="", token_uri=""), 1)
    out = []
    assert google_cli.connect(ws, FakeRunner(), None, home, client_file=str(broken), out=out.append) == 1
    assert "client_secret, token_uri" in out[0] and SECRET not in out[0]
    assert not (home / ".config" / "gws" / "client_secret.json").exists()


def test_connect_reports_an_unfinished_sign_in(ws, home, tmp_path):
    source = write(tmp_path / "client.json", client_json(), 1)
    out = []
    code = google_cli.connect(ws, FakeRunner(), None, home, client_file=str(source), spawn=spawner([], code=124),
                              out=out.append)
    assert code == 1 and "didn't finish in time" in out[-1]


def test_status_lists_missing_permissions_and_never_tokens(fake):
    status = json.loads(status_json([gws.GMAIL_READONLY, gws.CALENDAR_READONLY, gws.DRIVE_READONLY]))
    status["access_token"] = "ya29.should-never-print"
    fake.on("gws", "auth", "status", result=Result(0, json.dumps(status)))
    out = []
    assert google_cli.show_status(fake, None, out.append) == 1
    text = "\n".join(out)
    assert "Missing permissions: make Gmail drafts" in text and "brain google connect" in text
    assert "ya29" not in text and "client_secret" not in text


def test_status_when_not_connected(fake):
    fake.on("gws", "auth", "status", result=Result(2, json.dumps({"error": {"code": 401, "message": "No credentials"}})))
    out = []
    assert google_cli.show_status(fake, None, out.append) == 1
    assert out[0].startswith("Google: not connected")


# ---------------------------------------------------------------- CLI


def test_cli_registers_google_and_draft_once():
    parser = build_parser()
    assert parser.parse_args(["google", "status"]).func is google_cli.cmd_status
    assert parser.parse_args(["google", "connect"]).func is google_cli.cmd_connect


def test_draft_email_cli(ws, tmp_path, monkeypatch, capsys):
    fake = FakeRunner().on("gws", "gmail", "users", "drafts", "create",
                           result=Result(0, json.dumps({"id": "r-77", "message": {"threadId": "t"}})))
    monkeypatch.setattr(google_cli, "_runner", lambda: fake)
    body = tmp_path / "body.txt"
    body.write_text("Hello there\n", encoding="utf-8")
    args = build_parser().parse_args(["--workspace", str(ws.root), "draft", "email", "--to", "a@example.com",
                                      "--subject", "Hi", "--body-file", str(body)])
    assert args.func(args) == 0
    out = capsys.readouterr().out
    assert "Draft saved in Gmail (not sent). Open Gmail → Drafts to review and send." in out
    assert "r-77" in out
    parsed = decode_raw(json_arg(fake.calls[0]["argv"], "--json")["message"]["raw"])
    assert parsed.get_content().replace("\r\n", "\n") == "Hello there\n"
    assert fake.calls[0]["env"]["PATH"].split(":")[0] == str(ws.kit_bin)  # the guard shim comes first


def test_draft_email_needs_a_subject_or_a_reply_to(ws, capsys):
    args = build_parser().parse_args(["--workspace", str(ws.root), "draft", "email", "--to", "a@example.com",
                                      "--body", "x"])
    assert args.func(args) == 2
    assert "--subject" in capsys.readouterr().err
