"""The .kit/bin/gws guard shim, run with bash against a fake real gws, plus gws.is_blocked
checked against the same case table so the two never drift apart."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from brainkit import gws

REPO = Path(__file__).resolve().parents[1]
SHIM = REPO / "kit" / ".kit" / "bin" / "gws"
BASH = shutil.which("bash")

# (args, blocked) — shared by the shim and gws.is_blocked.
CASES = [
    (["gmail", "+send", "--to", "a@example.com", "--subject", "x"], True),
    (["gmail", "+reply", "--message-id", "1", "--body", "ok"], True),
    (["gmail", "+reply-all", "--message-id", "1"], True),
    (["gmail", "+forward", "--message-id", "1", "--to", "b@example.com"], True),
    (["--format", "json", "gmail", "+send"], True),
    (["gmail", "--dry-run", "+reply"], True),
    (["gmail", "users", "messages", "send", "--params", "{}"], True),
    (["gmail", "users", "drafts", "send", "--json", '{"id": "r1"}'], True),
    (["auth", "export"], True),
    (["auth", "export", "--unmasked"], True),
    (["gmail", "+triage", "--query", "+send"], True),  # conservative: a bare +send word after gmail
    (["gmail", "+triage", "--max", "5", "--query", "subject:+send"], False),
    (["gmail", "+read", "--id", "abc", "--headers", "--format", "json"], False),
    (["gmail", "users", "drafts", "create", "--params", '{"userId": "me"}', "--json", "{}"], False),
    (["gmail", "users", "messages", "get", "--params", "{}"], False),
    (["gmail", "users", "messages", "list"], False),
    (["calendar", "+agenda", "--today", "--format", "json"], False),
    (["auth", "status"], False),
    (["auth", "login", "--scopes", "https://www.googleapis.com/auth/gmail.readonly"], False),
    (["+send", "gmail"], False),  # a helper before the service isn't a Gmail helper
    (["drive", "files", "list", "--params", '{"q": "users messages send"}'], False),
    (["schema", "gmail.users.messages.send"], False),
    (["--version"], False),
    # options between the command words must not hide a send (review finding 1)
    (["gmail", "users", "--format", "json", "messages", "send", "--params", "{}"], True),
    (["gmail", "users", "messages", "--format", "json", "send"], True),
    (["gmail", "users", "drafts", "--dry-run", "send", "--json", "{}"], True),
    (["--format", "json", "gmail", "users", "messages", "send"], True),
    (["gmail", "users", "messages.send"], True),
    (["auth", "--format", "json", "export"], True),
    (["gmail", "+triage", "--query", "send"], False),  # a query value, not a command word
    (["gmail", "users", "settings", "sendAs", "list"], False),
    (["gmail", "users", "messages", "list", "--params", '{"q": "send"}'], False),
    (["gmail", "users", "drafts", "create", "--format=json", "--json", "{}"], False),
]

needs_bash = pytest.mark.skipif(BASH is None, reason="bash not available")


@pytest.fixture
def home(tmp_path) -> Path:
    path = tmp_path / "home"
    path.mkdir()
    return path


def fake_gws(path: Path, exit_code: int = 0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('#!/bin/sh\nprintf "REAL"\nfor a in "$@"; do printf " [%s]" "$a"; done\nprintf "\\n"\n'
                    f"exit {exit_code}\n")
    path.chmod(0o755)
    return path


def run_shim(args, home: Path, real=None, shim: Path = SHIM):
    env = {"PATH": "/usr/bin:/bin", "HOME": str(home)}
    if real is not None:
        env["BRAIN_GWS_REAL"] = str(real)
    return subprocess.run([BASH, str(shim)] + list(args), env=env, capture_output=True, text=True, timeout=30)


def echoed(args) -> str:
    return "REAL" + "".join(f" [{a}]" for a in args) + "\n"


@needs_bash
@pytest.mark.parametrize("args,blocked", CASES, ids=lambda v: " ".join(v) if isinstance(v, list) else str(v))
def test_shim_blocks_or_passes_through(args, blocked, home, tmp_path):
    real = fake_gws(tmp_path / "real" / "gws")
    proc = run_shim(args, home, real)
    if blocked:
        assert proc.returncode == 3
        assert proc.stderr == gws.BLOCKED_MESSAGE + "\n"
        assert proc.stdout == ""  # the real gws never ran
    else:
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout == echoed(args)


@pytest.mark.parametrize("args,blocked", CASES, ids=lambda v: " ".join(v) if isinstance(v, list) else str(v))
def test_python_guard_matches_the_shim_table(args, blocked):
    assert gws.is_blocked(args) is blocked


@needs_bash
def test_default_real_path_is_home_local_bin(home):
    fake_gws(home / ".local" / "bin" / "gws")
    proc = run_shim(["auth", "status"], home)
    assert proc.returncode == 0 and proc.stdout == echoed(["auth", "status"])


@needs_bash
def test_exit_code_of_real_gws_passes_through(home, tmp_path):
    real = fake_gws(tmp_path / "gws-real", exit_code=7)
    assert run_shim(["gmail", "+triage"], home, real).returncode == 7


@needs_bash
def test_missing_real_gws(home, tmp_path):
    proc = run_shim(["auth", "status"], home, tmp_path / "nope")
    assert proc.returncode == 4
    assert "gws isn't installed" in proc.stderr and "brain tools install gws" in proc.stderr


@needs_bash
def test_refuses_a_real_path_that_is_the_shim_itself(home, tmp_path):
    link = tmp_path / "links" / "gws"
    link.parent.mkdir()
    os.symlink(str(SHIM), str(link))
    proc = run_shim(["auth", "status"], home, link)
    assert proc.returncode == 4 and "points back to the brain's guard" in proc.stderr


@needs_bash
def test_a_copy_of_the_shim_at_the_default_path_does_not_loop(home):
    copy = home / ".local" / "bin" / "gws"
    copy.parent.mkdir(parents=True)
    shutil.copy(str(SHIM), str(copy))
    copy.chmod(0o755)
    proc = run_shim(["auth", "status"], home, shim=copy)
    assert proc.returncode == 4 and "points back" in proc.stderr


def test_shim_is_executable_and_bash32_safe():
    assert os.access(str(SHIM), os.X_OK)
    source = SHIM.read_text(encoding="utf-8")
    assert source.startswith("#!/bin/bash\n")
    text = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("#"))
    for pattern in (r"declare -A", r"\$\{[^}]*,,\}", r"\$\{[^}]*\^\^\}", r"\bmapfile\b", r"\breadarray\b",
                    r"\|&", r"&>>", r"\bcoproc\b", r"\[\[ -v", r"readlink -f"):
        assert not re.search(pattern, text), f"bash 4+ (or GNU-only) construct in the shim: {pattern}"
