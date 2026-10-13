from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tarfile
import time

import pytest

from brainkit import manifest as manifest_mod
from release_helpers import REPO, build, build_module, make_source, write

FILES = {"AGENTS.md": "rules\n", "docs/guide.md": "guide\n", ".claude/skills/brief/SKILL.md": "s\n",
         ".kit/lib/brainkit/__pycache__/x.cpython-39.pyc": b"cache"}
TEMPLATES = {"me.md": "# Me\n", ".kit/config.json": "{}\n", "tasks/active/.keep": ""}
MIGRATIONS = {"0002_rename.py": "def migrate(workspace):\n    return 'ok'\n", "README.md": "notes\n"}


def source(tmp_path, name="src", version="0.3.1", **overrides):
    kwargs = dict(files=FILES, templates=TEMPLATES, migrations=MIGRATIONS)
    kwargs.update(overrides)
    return make_source(tmp_path / name, version, **kwargs)


def test_layout_and_metadata(tmp_path):
    src = source(tmp_path)
    paths = build(src, tmp_path / "dist")
    assert paths["tarball"].name == "brain-starter-v0.3.1.tar.gz"
    with tarfile.open(str(paths["tarball"])) as archive:
        members = archive.getmembers()
        names = [m.name for m in members]
        inside = json.loads(archive.extractfile("brain-starter-v0.3.1/manifest.json").read())
    top = "brain-starter-v0.3.1"
    assert names[0] == top and all(n == top or n.startswith(top + "/") for n in names)
    assert names == sorted(names)
    for expected in ("kit/AGENTS.md", "kit/.kit/bin/brain", "templates/.kit/config.json",
                     "templates/tasks/active/.keep", "migrations/0002_rename.py",
                     "migrations/README.md", "manifest.json"):
        assert f"{top}/{expected}" in names
    assert not any("__pycache__" in n or n.endswith(".pyc") for n in names)
    assert not any(n.endswith("install.sh") for n in names)
    by_name = {m.name: m for m in members}
    assert by_name[f"{top}/kit/.kit/bin/brain"].mode == 0o755
    assert by_name[f"{top}/kit/AGENTS.md"].mode == 0o644
    assert by_name[f"{top}/kit/docs"].isdir() and by_name[f"{top}/kit/docs"].mode == 0o755
    for member in members:
        assert member.mtime == build_module().FIXED_MTIME
        assert (member.uid, member.gid, member.uname, member.gname) == (0, 0, "", "")
        assert member.isdir() or member.isreg()
    assert inside == manifest_mod.build_manifest(src)
    assert inside["migrations"] == ["0002_rename"]


def test_two_builds_are_byte_identical(tmp_path):
    src = source(tmp_path)
    first = build(src, tmp_path / "one")
    later = time.time() + 3600
    for path in src.rglob("*"):
        os.utime(str(path), (later, later))  # a fresh checkout has different mtimes
    second = build(src, tmp_path / "two")
    for key in ("tarball", "sums", "install_sh"):
        assert first[key].read_bytes() == second[key].read_bytes(), key


def test_sums_and_installer(tmp_path):
    src = source(tmp_path)
    paths = build(src, tmp_path / "dist")
    lines = paths["sums"].read_text().splitlines()
    expected = [f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}"
                for p in (paths["tarball"], paths["install_sh"])]  # sorted by file name
    assert lines == expected
    assert paths["install_sh"].read_bytes() == (src / "install.sh").read_bytes()
    assert os.stat(paths["install_sh"]).st_mode & 0o777 == 0o755


def test_version_flag_must_match(tmp_path):
    src = source(tmp_path)
    main = build_module().main
    assert main(["--root", str(src), "--out", str(tmp_path / "d"), "--version", "9.9.9"]) == 2
    assert main(["--root", str(src), "--out", str(tmp_path / "d"), "--version", "v0.3.1"]) == 0


@pytest.mark.parametrize("change, message", [
    (lambda src: write(src / "templates" / "AGENTS.md", "x"), "both kit file and template"),
    (lambda src: write(src / "migrations" / "2_bad.py", "def migrate(w):\n    pass\n"), "0002_short_name"),
    (lambda src: write(src / "migrations" / "0003_empty.py", "X = 1\n"), "migrate"),
    (lambda src: os.symlink("AGENTS.md", str(src / "kit" / "LINK.md")), "symlink"),
    (lambda src: write(src / "kit" / ".kit" / "VERSION", "one\n"), "1.2.3"),
    (lambda src: os.unlink(str(src / "install.sh")), "install.sh"),
    (lambda src: os.unlink(str(src / "kit" / ".kit" / "bin" / "brain")), "bin/brain"),
], ids=["overlap", "migration-name", "migration-without-migrate", "symlink", "bad-version",
        "no-installer", "no-entry-point"])
def test_bad_trees_are_refused(tmp_path, capsys, change, message):
    src = source(tmp_path)
    change(src)
    assert build_module().main(["--root", str(src), "--out", str(tmp_path / "d")]) == 2
    assert message in capsys.readouterr().err


def test_a_file_changing_mid_build_fails_loudly(tmp_path, monkeypatch):
    src = source(tmp_path)
    module = build_module()
    real = module.manifest_mod.build_manifest

    def stale_manifest(root):
        data = real(root)
        write(src / "kit" / "AGENTS.md", "edited while the build ran\n")  # after hashing
        return data

    monkeypatch.setattr(module.manifest_mod, "build_manifest", stale_manifest)
    with pytest.raises(module.BuildError, match="changed during the build"):
        module.build(src, tmp_path / "dist")
    assert list((tmp_path / "dist").iterdir()) == []  # no tarball, no .part left behind


def test_command_line(tmp_path):
    src = source(tmp_path)
    out = tmp_path / "dist"
    result = subprocess.run([sys.executable, str(REPO / "tools" / "build_release.py"), "--root",
                             str(src), "--out", str(out)], capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    assert str(out / "brain-starter-v0.3.1.tar.gz") in result.stdout
    assert sorted(p.name for p in out.iterdir()) == ["SHA256SUMS", "brain-starter-v0.3.1.tar.gz",
                                                     "install.sh"]


def test_the_real_repo_builds(repo, tmp_path):
    paths = build(repo, tmp_path / "dist")
    version = (repo / "kit" / ".kit" / "VERSION").read_text().strip()
    top = f"brain-starter-v{version}"
    with tarfile.open(str(paths["tarball"])) as archive:
        inside = json.loads(archive.extractfile(f"{top}/manifest.json").read())
        brain = archive.getmember(f"{top}/kit/.kit/bin/brain")
    assert inside["version"] == version and brain.mode == 0o755
    assert inside["kit_files"][".kit/bin/brain"]["mode"] == "0755"
    assert ".kit/config.json" in inside["templates"]
