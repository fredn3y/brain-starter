from __future__ import annotations

import io
import json
import logging
import os

import pytest

from brainkit import snapshot as snapshot_mod
from brainkit.cli import main as cli_main
from brainkit.config import Config
from brainkit.cli import build_parser
from brainkit.install import (BLOCK_BEGIN, BLOCK_END, EX_TOOLS, ensure_path_block, path_block,
                              run_install, split_names)
from brainkit.lock import try_lock
from brainkit.paths import Workspace
from brainkit.runner import FakeRunner, Result
from release_helpers import make_release_dir, tree, write

LOG = logging.getLogger("test-install")
FILES = {"AGENTS.md": "rules\n", ".claude/skills/brief/SKILL.md": "brief\n",
         ".kit/codex/brain-starter.rules": "rules for codex\n"}
TEMPLATES = {"me.md": "# Me\n", ".kit/config.json": "{}\n", "tasks/active/.keep": ""}
TOOLS = {"tools": [
    {"name": "claude", "required": True, "default": True, "check": ["claude", "--version"],
     "install": {"type": "script", "url": "https://claude.ai/install.sh", "shell": "bash"}},
    {"name": "helper", "required": False, "check": ["helper", "--version"],
     "install": {"type": "manual", "instructions": "Install helper from its website."}},
    {"name": "gws", "required": False, "default": False, "check": ["gws", "--version"],
     "install": {"type": "archive-binary", "version": "0.0.1", "binary": "gws",
                 "dest": "~/.local/bin/gws",
                 "assets": {"arm64": {"url": "TODO", "sha256": "TODO"},
                            "x86_64": {"url": "TODO", "sha256": "TODO"}}}},
    {"name": "codex", "required": False, "default": False, "check": ["codex", "--version"],
     "install": {"type": "manual", "instructions": "Install Codex from its website."}},
]}


def claude_present(fake=None):
    fake = fake or FakeRunner()
    return fake.on("uname", "-m", result=Result(0, "arm64\n")).on(
        "claude", "--version", result=Result(0, "2.1.0 (Claude Code)\n"))


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    path = tmp_path / "home"
    path.mkdir()
    monkeypatch.setenv("HOME", str(path))
    monkeypatch.delenv("BRAIN_HOME", raising=False)
    return path


@pytest.fixture
def release(tmp_path):
    files = dict(FILES, **{".kit/tools.json": json.dumps(TOOLS)})
    return make_release_dir(tmp_path / "rel", "0.1.0", files=files, templates=TEMPLATES)


def install(release, ws, home, runner=None, **kwargs):
    out = io.StringIO()
    kwargs.setdefault("no_tools", True)
    code = run_install(release, ws, home, runner or FakeRunner(), LOG, out=out, **kwargs)
    return code, out.getvalue()


def test_config_template_equals_config_defaults(repo):
    template = json.loads((repo / "templates" / ".kit" / "config.json").read_text())
    assert template == Config().to_dict()


def test_fresh_install(release, home):
    ws = Workspace(home / "brain")
    code, text = install(release, ws, home)
    assert code == 0, text
    assert ws.manifest.is_file() and ws.version_file.read_text().strip() == "0.1.0"
    for rel in TEMPLATES:
        assert (ws.root / rel).exists()
    link = home / ".local" / "bin" / "brain"
    assert link.is_symlink() and os.readlink(str(link)) == str(ws.kit_bin / "brain")
    profile = (home / ".zprofile").read_text()
    assert profile == (f'{BLOCK_BEGIN}\nexport PATH="$HOME/brain/.kit/bin:$HOME/.local/bin:$PATH"\n'
                       f"{BLOCK_END}\n")
    assert (home / ".codex" / "rules" / "brain-starter.rules").read_text() == "rules for codex\n"
    for words in ("new Terminal window", "the `brain` and `claude` commands", "Claude account",
                  "cd ~/brain && claude", "T3 Code", "set me up", "~/brain", "Your brain is ready"):
        assert words in text
    assert "work account" not in text and "codex" not in text.lower()


def test_rerunning_install_is_safe(release, home):
    ws = Workspace(home / "brain")
    assert install(release, ws, home)[0] == 0
    write(ws.root / "me.md", "her answers\n")
    write(ws.root / "AGENTS.md", "her edited rules\n")
    write(ws.root / "tasks/active/plan.md", "# Plan\n")
    code, text = install(release, ws, home)
    assert code == 0, text
    assert (ws.root / "me.md").read_text() == "her answers\n"
    assert (ws.root / "AGENTS.md").read_text() == "her edited rules\n"
    assert (ws.root / "AGENTS.md.new").read_text() == "rules\n"
    assert (ws.root / "tasks/active/plan.md").read_text() == "# Plan\n"
    assert (home / ".zprofile").read_text().count(BLOCK_BEGIN) == 1
    assert "AGENTS.md.new" in text


def test_fresh_install_needs_no_snapshot(release, home):
    ws = Workspace(home / "brain")
    assert install(release, ws, home)[0] == 0
    assert snapshot_mod.list_snapshots(ws) == []


def test_reinstall_waits_for_the_lock_and_snapshots_first(release, home):
    ws = Workspace(home / "brain")
    assert install(release, ws, home)[0] == 0
    write(ws.root / "tasks/active/plan.md", "# Plan\n")
    before = tree(ws.root)
    with try_lock(ws.lock_file) as held:  # a catch-up is running
        assert held
        code, text = install(release, ws, home)
    assert code == 75 and "Try again in a few minutes" in text  # EX_TEMPFAIL: install.sh retries
    assert tree(ws.root) == before and snapshot_mod.list_snapshots(ws) == []
    code, text = install(release, ws, home)
    assert code == 0, text
    [snap] = snapshot_mod.list_snapshots(ws)
    assert snap.name.endswith("-v0.1.0-to-v0.1.0-reinstall.tgz")


def test_reinstall_failure_puts_everything_back(release, home, tmp_path):
    ws = Workspace(home / "brain")
    assert install(release, ws, home)[0] == 0
    write(ws.root / "me.md", "her answers\n")
    broken = ("from pathlib import Path\n\ndef migrate(workspace):\n"
              "    (Path(workspace) / 'me.md').write_text('clobbered')\n"
              "    raise RuntimeError('migration exploded')\n")
    files = dict(FILES, **{"AGENTS.md": "rules v2\n", "docs/new.md": "new\n",
                           ".kit/tools.json": json.dumps(TOOLS)})
    v2 = make_release_dir(tmp_path / "rel2", "0.2.0", files=files,
                          templates=dict(TEMPLATES, **{"voice.md": "# Voice\n"}),
                          migrations={"0002_broken.py": broken})
    before = tree(ws.root)
    code, text = install(v2, ws, home)
    assert code == 1 and "migration exploded" in text and "put back exactly" in text
    assert tree(ws.root) == before
    assert (ws.root / "me.md").read_text() == "her answers\n"


def test_existing_cli_link_is_replaced_but_a_real_file_is_not(release, home, tmp_path):
    ws = Workspace(home / "brain")
    link = home / ".local" / "bin" / "brain"
    link.parent.mkdir(parents=True)
    os.symlink(str(tmp_path / "old-brain"), str(link))
    assert install(release, ws, home)[0] == 0
    assert os.readlink(str(link)) == str(ws.kit_bin / "brain")
    link.unlink()
    link.write_text("#!/bin/sh\necho mine\n")
    code, text = install(release, ws, home)
    assert code == 0 and "left alone" in text
    assert link.read_text() == "#!/bin/sh\necho mine\n"


def test_custom_workspace_gets_an_absolute_quoted_path(release, home, tmp_path):
    ws = Workspace(tmp_path / "Work $tuff" / "brain")
    assert install(release, ws, home)[0] == 0
    profile = (home / ".zprofile").read_text()
    escaped = str(ws.kit_bin).replace("$", "\\$")
    assert f'export PATH="{escaped}:$HOME/.local/bin:$PATH"' in profile


def test_zprofile_keeps_her_lines_and_refreshes_one_block(home, tmp_path):
    profile = home / ".zprofile"
    profile.write_text(
        "export EDITOR=nano\n"
        f"{BLOCK_BEGIN}\nexport PATH=\"/old/place/.kit/bin:$PATH\"\n{BLOCK_END}\n"
        "alias ll='ls -l'\n"
        f"{BLOCK_BEGIN}\nexport PATH=\"/duplicate:$PATH\"\n{BLOCK_END}\n"
        "eval \"$(/opt/homebrew/bin/brew shellenv)\""
    )
    ws = Workspace(home / "brain")
    ensure_path_block(ws, home)
    text = profile.read_text()
    assert text.count(BLOCK_BEGIN) == 1 and "/old/place" not in text and "/duplicate" not in text
    assert text.startswith("export EDITOR=nano\n" + path_block(ws, home))
    assert "alias ll='ls -l'\n" in text and text.endswith('eval "$(/opt/homebrew/bin/brew shellenv)"')
    once = text
    ensure_path_block(ws, home)
    assert profile.read_text() == once


def test_zprofile_appends_after_existing_content(home):
    profile = home / ".zprofile"
    profile.write_text("export EDITOR=nano")  # no trailing newline
    ensure_path_block(Workspace(home / "brain"), home)
    assert profile.read_text().startswith("export EDITOR=nano\n\n# >>> brain >>>\n")


def test_zprofile_symlink_is_written_through(home, tmp_path):
    real = tmp_path / "dotfiles" / "zprofile"
    write(real, "export EDITOR=vim\n")
    os.symlink(str(real), str(home / ".zprofile"))
    ensure_path_block(Workspace(home / "brain"), home)
    assert (home / ".zprofile").is_symlink()
    assert BLOCK_BEGIN in real.read_text() and real.read_text().startswith("export EDITOR=vim\n")


def test_no_path_leaves_zprofile_alone(release, home):
    assert install(release, Workspace(home / "brain"), home, no_path=True)[0] == 0
    assert not (home / ".zprofile").exists()


def test_fresh_install_gets_only_the_standard_tools(release, home):
    fake = claude_present()
    code, text = install(release, Workspace(home / "brain"), home, runner=fake, no_tools=False)
    assert code == 0, text
    assert "claude: already installed (2.1.0 (Claude Code))" in text
    assert "helper: to do by hand (Install helper from its website.)" in text  # no "default": standard
    assert "gws" not in text and "codex" not in text.lower()  # add-ons wait until asked for
    assert not any(call["argv"][0] in ("curl", "gws", "codex") for call in fake.calls)


def test_add_on_problems_are_reported_not_fatal(release, home):
    fake = claude_present()
    code, text = install(release, Workspace(home / "brain"), home, runner=fake, no_tools=False,
                         extra_tools=["gws,codex", "nope"])
    assert code == 0, text
    assert "gws: not ready yet" in text and "Ask your consultant" in text
    assert "codex: to do by hand (Install Codex from its website.)" in text
    assert "Not a tool the brain knows, so skipped: nope" in text
    assert "Something above didn't install" in text and "Your brain is ready" in text
    assert "codex login" in text  # the Codex step only shows when Codex was asked for
    assert not any(call["argv"][0] == "curl" for call in fake.calls)


def test_claude_failing_to_install_is_said_plainly(release, home):
    fake = FakeRunner().on("bash", "-c", result=Result(1, "", "curl: (6) Could not resolve host"))
    ws = Workspace(home / "brain")
    code, text = install(release, ws, home, runner=fake, no_tools=False)
    assert code == EX_TOOLS == 3
    assert "claude: FAILED" in text and "Could not resolve host" in text
    assert "Claude Code didn't install, so your brain can't be used yet." in text
    assert "running the install again is safe" in text
    assert "is ready" not in text and "Next steps" not in text
    assert ws.manifest.is_file() and (home / ".local" / "bin" / "brain").is_symlink()
    fake.on("claude", "--version", result=Result(0, "2.1.0\n"))  # the second try works
    code, text = install(release, ws, home, runner=fake, no_tools=False)
    assert code == 0 and "Your brain is ready" in text


def test_with_option_and_name_splitting():
    args = build_parser().parse_args(["install", "--release-dir", "x", "--with", "gws,codex",
                                      "--with", "codex t3"])
    assert args.extra_tools == ["gws,codex", "codex t3"]
    assert split_names(args.extra_tools) == ["gws", "codex", "t3"]
    assert build_parser().parse_args(["install", "--release-dir", "x"]).extra_tools == []


def test_cli_install(release, home, tmp_path):
    ws_root = tmp_path / "elsewhere" / "brain"
    code = cli_main(["install", "--release-dir", str(release), "--workspace", str(ws_root),
                     "--home", str(home), "--no-tools"])
    assert code == 0
    assert (ws_root / ".kit" / "manifest.json").is_file()
    assert os.readlink(str(home / ".local" / "bin" / "brain")) == str(ws_root / ".kit/bin/brain")


def test_cli_install_defaults_to_home_brain(release, home):
    assert cli_main(["install", "--release-dir", str(release), "--home", str(home),
                     "--no-tools", "--no-path"]) == 0
    assert (home / "brain" / ".kit" / "manifest.json").is_file()


def test_broken_release_fails_cleanly(tmp_path, home):
    broken = tmp_path / "broken"
    broken.mkdir()
    code, text = install(broken, Workspace(home / "brain"), home)
    assert code == 1 and "Setup failed" in text and "consultant" in text



def test_install_makes_the_brain_folder_owner_only(tmp_path):
    import logging
    import os
    import stat

    from release_helpers import make_release_dir
    from brainkit.install import run_install
    from brainkit.paths import Workspace
    from brainkit.runner import FakeRunner

    rel = make_release_dir(tmp_path / "rel", "0.1.0")
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    ws = Workspace(home / "brain")
    code = run_install(rel, ws, home, FakeRunner(), logging.getLogger("t"), no_tools=True, no_path=True)
    assert code == 0
    assert stat.S_IMODE(os.stat(str(ws.root)).st_mode) == 0o700
