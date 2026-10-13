from __future__ import annotations

import hashlib
import os

import pytest

from brainkit import manifest as m
from release_helpers import make_source, write


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def test_hash_file_matches_hashlib(tmp_path):
    path = write(tmp_path / "f.bin", b"x" * 200_000)
    assert m.hash_file(path) == hashlib.sha256(b"x" * 200_000).hexdigest()


def test_build_manifest_shape_modes_and_skips(tmp_path):
    root = make_source(
        tmp_path / "src", "0.3.1",
        files={
            "AGENTS.md": "rules\n",
            "docs/guide.md": "guide\n",
            "scripts/run.sh": "#!/bin/sh\n",
            ".kit/lib/brainkit/x.py": "X = 1\n",
            ".kit/lib/brainkit/__pycache__/x.cpython-39.pyc": b"cache",
            ".kit/lib/brainkit/stale.pyc": b"cache",
            ".DS_Store": b"finder",
            "docs/.DS_Store": b"finder",
        },
        templates={"me.md": "# Me\n", ".kit/config.json": "{}\n", "tasks/active/.keep": ""},
        migrations={"0003_more.py": "def migrate(ws):\n    return ''\n",
                    "0002_rename_x.py": "def migrate(ws):\n    return ''\n",
                    "README.md": "notes\n", "helper.txt": "x\n"},
    )
    os.chmod(root / "kit" / "scripts" / "run.sh", 0o755)
    os.chmod(root / "kit" / ".kit" / "bin" / "brain", 0o644)  # a checkout that lost the x bit

    data = m.build_manifest(root)

    assert data["name"] == "brain-starter" and data["version"] == "0.3.1"
    assert sorted(data["kit_files"]) == [".kit/VERSION", ".kit/bin/brain", ".kit/lib/brainkit/x.py",
                                         "AGENTS.md", "docs/guide.md", "scripts/run.sh"]
    assert data["kit_files"]["AGENTS.md"] == {"sha256": sha("rules\n"), "mode": "0644"}
    assert data["kit_files"][".kit/bin/brain"]["mode"] == "0755"
    assert data["kit_files"]["scripts/run.sh"]["mode"] == "0755"
    assert data["templates"] == [".kit/config.json", "me.md", "tasks/active/.keep"]
    assert data["migrations"] == ["0002_rename_x", "0003_more"]
    assert m.validate(data) is data


def test_symlinks_are_refused(tmp_path):
    root = make_source(tmp_path / "src", "0.1.0", files={"docs/a.md": "a\n"})
    os.symlink("a.md", str(root / "kit" / "docs" / "b.md"))
    with pytest.raises(m.ManifestError, match="symlink"):
        m.build_manifest(root)


def test_symlinked_folder_is_refused(tmp_path):
    root = make_source(tmp_path / "src", "0.1.0", files={"docs/a.md": "a\n"})
    os.symlink("docs", str(root / "kit" / "more-docs"))
    with pytest.raises(m.ManifestError, match="symlink"):
        m.build_manifest(root)


def test_missing_version_is_an_error(tmp_path):
    (tmp_path / "kit").mkdir()
    with pytest.raises(m.ManifestError):
        m.build_manifest(tmp_path)


def test_save_is_atomic_and_round_trips(tmp_path):
    data = m.empty()
    data["kit_files"]["AGENTS.md"] = {"sha256": sha("x"), "mode": "0644"}
    target = tmp_path / "deep" / "manifest.json"
    m.save(target, data)
    assert m.load(target) == data
    assert os.stat(target).st_mode & 0o777 == 0o644
    assert sorted(p.name for p in target.parent.iterdir()) == ["manifest.json"]  # no temp left
    m.save(target, m.empty())
    assert m.load(target)["kit_files"] == {}


def test_load_optional_missing_is_none(tmp_path):
    assert m.load_optional(tmp_path / "nope.json") is None


@pytest.mark.parametrize("bad", ["../x", "/etc/passwd", "a/../../b", "a//b", "", "./a", "a/."])
def test_validate_rejects_escaping_paths(bad):
    data = m.empty()
    data["kit_files"][bad] = {"sha256": sha("x"), "mode": "0644"}
    with pytest.raises(m.ManifestError):
        m.validate(data)
    data = m.empty()
    data["templates"] = [bad]
    with pytest.raises(m.ManifestError):
        m.validate(data)


def test_validate_rejects_bad_hash_mode_name_and_migrations():
    for meta in ({"sha256": "abc", "mode": "0644"}, {"sha256": sha("x"), "mode": "0777"}):
        data = m.empty()
        data["kit_files"]["a"] = meta
        with pytest.raises(m.ManifestError):
            m.validate(data)
    with pytest.raises(m.ManifestError):
        m.validate(dict(m.empty(), name="other"))
    with pytest.raises(m.ManifestError):
        m.validate(dict(m.empty(), migrations=["2_bad"]))


def test_real_repo_manifest(repo):
    data = m.build_manifest(repo)
    kit = data["kit_files"]
    assert kit[".kit/bin/brain"]["mode"] == "0755"
    assert ".kit/VERSION" in kit and ".kit/tools.json" in kit
    assert ".kit/config.json" in data["templates"]
    assert not set(kit) & set(data["templates"])
    assert not any("__pycache__" in rel or rel.endswith(".pyc") for rel in kit)
    m.validate(data)
