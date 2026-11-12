"""`brain trash`: moves into a fake home's `.Trash`, never deletes, refuses what it must."""

from __future__ import annotations

import json
import os
from datetime import datetime

import pytest

from brainkit import cli, trash
from brainkit.paths import Workspace


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    (h / ".Trash").mkdir(parents=True)
    return h


@pytest.fixture
def brain(home):
    ws = Workspace(home / "brain")
    (ws.root / ".kit").mkdir(parents=True)
    (ws.root / "tasks" / "archive").mkdir(parents=True)
    (ws.root / ".claude" / "skills" / "onboard").mkdir(parents=True)
    (ws.root / ".claude" / "skills" / "my-notes").mkdir(parents=True)
    (ws.root / "AGENTS.md").write_text("kit")
    (ws.root / "tasks" / "TEMPLATE.md").write_text("kit")
    (ws.root / ".claude" / "skills" / "onboard" / "SKILL.md").write_text("kit")
    (ws.root / ".claude" / "skills" / "my-notes" / "SKILL.md").write_text("theirs")
    (ws.root / ".kit" / "manifest.json").write_text(json.dumps({"kit_files": {
        "AGENTS.md": {}, "tasks/TEMPLATE.md": {}, ".claude/skills/onboard/SKILL.md": {}}}))
    return ws


def run(paths, brain, home, cwd=None, platform="darwin"):
    return trash.trash_paths(paths, brain, home, cwd=cwd or brain.root, platform=platform)


def test_moves_a_folder_and_a_file_into_the_trash(brain, home):
    folder = brain.root / "tasks" / "archive" / "old-project"
    (folder / "inner").mkdir(parents=True)
    (folder / "inner" / "a.txt").write_text("keep me")
    (brain.root / "notes.txt").write_text("n")
    code, lines = run(["tasks/archive/old-project", "notes.txt"], brain, home)
    assert code == 0
    assert lines == ["Moved tasks/archive/old-project to the Trash.", "Moved notes.txt to the Trash."]
    assert not folder.exists() and not (brain.root / "notes.txt").exists()
    assert (home / ".Trash" / "old-project" / "inner" / "a.txt").read_text() == "keep me"
    assert (home / ".Trash" / "notes.txt").read_text() == "n"


def test_a_name_already_in_the_trash_gets_a_number(brain, home):
    (home / ".Trash" / "notes.txt").write_text("older")
    (home / ".Trash" / "notes 2.txt").write_text("old")
    (home / ".Trash" / "drafts").mkdir()
    (brain.root / "notes.txt").write_text("new")
    (brain.root / "drafts").mkdir()
    code, lines = run(["notes.txt", "drafts"], brain, home)
    assert code == 0
    assert lines == ['Moved notes.txt to the Trash as "notes 3.txt" (that name was already there).',
                     'Moved drafts to the Trash as "drafts 2" (that name was already there).']
    assert (home / ".Trash" / "notes.txt").read_text() == "older"  # nothing overwritten
    assert (home / ".Trash" / "notes 3.txt").read_text() == "new"
    assert (home / ".Trash" / "drafts 2").is_dir()


def test_free_name_falls_back_to_a_time_stamp_when_the_trash_cannot_be_read(home, monkeypatch):
    monkeypatch.setattr(trash, "_exists", lambda path: None)
    name = trash.free_name(home / ".Trash", "notes.txt", now=datetime(2026, 11, 12, 9, 30, 5, 7))
    assert name == "notes 2026-11-12 09.30.05.000007.txt"


def test_dotfiles_keep_their_name_shape(home):
    (home / ".Trash" / ".env").write_text("x")
    assert trash.free_name(home / ".Trash", ".env") == ".env 2"


def test_works_on_paths_outside_the_brain_but_inside_home(brain, home, tmp_path):
    other = home / "Desktop" / "old"
    other.mkdir(parents=True)
    code, lines = run([str(other)], brain, home, cwd=tmp_path)
    assert code == 0 and lines == [f"Moved {other} to the Trash."]
    assert (home / ".Trash" / "old").is_dir()


@pytest.mark.parametrize("raw, why", [
    ("..", "it is the home folder"),
    (".", "it is the brain folder"),
    ("AGENTS.md", "it is part of the brain kit"),
    ("tasks/TEMPLATE.md", "it is part of the brain kit"),
    ("tasks", "it is part of the brain kit"),
    (".claude", "it is part of the brain kit"),
    (".claude/skills/onboard", "it is part of the brain kit"),
    (".kit", "it is part of the brain kit"),
    (".kit/manifest.json", "it is part of the brain kit"),
    ("../.Trash", "it is already in the Trash"),
])
def test_refuses_what_it_must_and_moves_nothing(brain, home, raw, why):
    before = sorted(p.name for p in (home / ".Trash").iterdir())
    code, lines = run([raw], brain, home)
    assert code == 1
    assert lines == [f"Not moved: {raw} ({why})."]
    assert sorted(p.name for p in (home / ".Trash").iterdir()) == before


def test_refuses_a_folder_holding_the_brain(home, tmp_path):
    ws = Workspace(home / "work" / "brain")
    ws.root.mkdir(parents=True)
    code, lines = run([str(home / "work")], ws, home, cwd=tmp_path)
    assert code == 1 and "it holds the brain folder" in lines[0]
    assert ws.root.is_dir()


def test_refuses_anything_outside_home_and_things_already_trashed(brain, home, tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (home / ".Trash" / "gone.txt").write_text("x")
    code, lines = run([str(outside), str(home / ".Trash" / "gone.txt")], brain, home)
    assert code == 1
    assert lines == [f"Not moved: {outside} (it is outside the home folder).",
                     f"Not moved: {home / '.Trash' / 'gone.txt'} (it is already in the Trash)."]
    assert outside.is_dir() and (home / ".Trash" / "gone.txt").is_file()


def test_refuses_a_path_reaching_outside_home_through_a_link(brain, home, tmp_path):
    outside = tmp_path / "elsewhere"
    (outside / "data").mkdir(parents=True)
    os.symlink(str(outside), str(brain.root / "link"))
    code, lines = run(["link/data"], brain, home)
    assert code == 1 and "outside the home folder" in lines[0]
    assert (outside / "data").is_dir()


def test_moves_a_link_itself_not_what_it_points_to(brain, home, tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    os.symlink(str(outside), str(brain.root / "shortcut"))
    code, _ = run(["shortcut"], brain, home)
    assert code == 0
    assert os.path.islink(str(home / ".Trash" / "shortcut")) and outside.is_dir()


def test_their_own_skill_can_be_trashed(brain, home):
    code, lines = run([".claude/skills/my-notes"], brain, home)
    assert code == 0 and lines == ["Moved .claude/skills/my-notes to the Trash."]


def test_missing_paths_say_so_and_the_rest_still_move(brain, home):
    (brain.root / "a.txt").write_text("a")
    code, lines = run(["nope.txt", "a.txt"], brain, home)
    assert code == 1
    assert lines == ["Not moved: nothing is at nope.txt.", "Moved a.txt to the Trash."]


def test_kit_files_stay_protected_without_a_manifest(home, tmp_path):
    ws = Workspace(home / "brain")
    ws.root.mkdir()
    (ws.root / "AGENTS.md").write_text("kit")
    code, lines = run(["AGENTS.md"], ws, home)
    assert code == 1 and "part of the brain kit" in lines[0]


def test_another_drive_is_refused_plainly(brain, home, monkeypatch):
    (brain.root / "big").mkdir()

    def cross_device(src, dst):
        raise OSError(18, "Invalid cross-device link")

    monkeypatch.setattr(trash.os, "rename", cross_device)
    code, lines = run(["big"], brain, home)
    assert code == 1 and lines == ["Not moved: big is on another drive. Ask the user to drag it to the Trash."]
    assert (brain.root / "big").is_dir()


def test_refuses_off_a_mac_and_touches_nothing(brain, home):
    (brain.root / "a.txt").write_text("a")
    code, lines = run(["a.txt"], brain, home, platform="linux")
    assert code == 2 and lines == ["`brain trash` only works on a Mac. Nothing was moved."]
    assert (brain.root / "a.txt").is_file()


def test_cli_wires_up_brain_trash(brain, home, monkeypatch, capsys):
    (brain.root / "a.txt").write_text("a")
    monkeypatch.setattr(trash, "_platform", lambda: "darwin")
    monkeypatch.setattr(trash, "_home", lambda: home)
    monkeypatch.chdir(brain.root)
    assert cli.main(["--workspace", str(brain.root), "trash", "a.txt"]) == 0
    assert capsys.readouterr().out == "Moved a.txt to the Trash.\n"
    monkeypatch.setattr(trash, "_platform", lambda: "linux")
    assert cli.main(["--workspace", str(brain.root), "trash", "x"]) == 2
    assert "only works on a Mac" in capsys.readouterr().err


def test_messages_are_plain():
    import inspect

    source = inspect.getsource(trash)
    assert "—" not in source
