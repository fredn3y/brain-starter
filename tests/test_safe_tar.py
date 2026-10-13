from __future__ import annotations

import io
import os
import stat
import tarfile
import zipfile

import pytest

from brainkit import safe_tar
from brainkit.safe_tar import UnsafeArchiveError, member_names, safe_extract, safe_unzip


@pytest.fixture(params=["data-filter", "own-checks-only"])
def extraction(request, monkeypatch):
    """Run each test with tarfile's data filter and without it (Apple's Python 3.9 may lack it)."""
    if request.param == "own-checks-only":
        monkeypatch.setattr(safe_tar, "_HAS_DATA_FILTER", False)
    elif not safe_tar._HAS_DATA_FILTER:
        pytest.skip("this Python has no tarfile.data_filter")
    return request.param


def make_tar(path, members):
    """members: (name, kind, payload, mode); kind in file/dir/sym/hard/chr/blk/fifo."""
    types = {"dir": tarfile.DIRTYPE, "sym": tarfile.SYMTYPE, "hard": tarfile.LNKTYPE,
             "chr": tarfile.CHRTYPE, "blk": tarfile.BLKTYPE, "fifo": tarfile.FIFOTYPE}
    with tarfile.open(str(path), "w:gz") as archive:
        for name, kind, payload, mode in members:
            info = tarfile.TarInfo(name)
            info.mode = mode
            if kind == "file":
                data = payload.encode() if isinstance(payload, str) else payload
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
                continue
            info.type = types[kind]
            if kind in ("sym", "hard"):
                info.linkname = payload
            archive.addfile(info)
    return path


def test_extracts_files_dirs_and_inside_links(tmp_path, extraction):
    tar = make_tar(tmp_path / "ok.tgz", [
        ("top/", "dir", None, 0o755),
        ("top/bin/brain", "file", "#!/bin/sh\n", 0o755),
        ("top/docs/a.md", "file", "a\n", 0o644),
        ("top/.claude/skills/brief/SKILL.md", "file", "s\n", 0o644),
        ("top/.agents/skills/brief", "sym", "../../.claude/skills/brief", 0o777),
    ])
    dest = tmp_path / "out"
    names = safe_extract(tar, dest)
    assert "top/docs/a.md" in names
    assert (dest / "top/docs/a.md").read_text() == "a\n"
    assert os.stat(dest / "top/bin/brain").st_mode & 0o777 == 0o755
    assert os.stat(dest / "top/docs/a.md").st_mode & 0o777 == 0o644
    assert os.readlink(str(dest / "top/.agents/skills/brief")) == "../../.claude/skills/brief"
    assert (dest / "top/.agents/skills/brief/SKILL.md").read_text() == "s\n"


@pytest.mark.parametrize("members", [
    [("/etc/evil", "file", "x", 0o644)],
    [("ok.txt", "file", "x", 0o644), ("../evil", "file", "x", 0o644)],
    [("a/../../evil", "file", "x", 0o644)],
    [("dev", "chr", None, 0o644)],
    [("disk", "blk", None, 0o644)],
    [("pipe", "fifo", None, 0o644)],
    [("ok.txt", "file", "x", 0o644), ("hard", "hard", "ok.txt", 0o644)],
    [("abs-link", "sym", "/etc/passwd", 0o777)],
    [("up-link", "sym", "../outside", 0o777)],
    [("a/b/deep-link", "sym", "../../../outside", 0o777)],
    # a member written through a link from the same archive
    [("sub/", "dir", None, 0o755), ("via", "sym", "sub", 0o777), ("via/file", "file", "x", 0o644)],
    # a link resolving through another link: x/b -> .. (inside), c -> x/b/.. (outside)
    [("x/", "dir", None, 0o755), ("x/b", "sym", "..", 0o777), ("c", "sym", "x/b/..", 0o777)],
    # the classic chain: d -> ., then d/d/d/e -> ../../x
    [("d", "sym", ".", 0o777), ("d/d/d/e", "sym", "../../x", 0o777)],
], ids=["absolute", "dotdot", "dotdot-inner", "chardev", "blockdev", "fifo", "hardlink",
        "abs-symlink", "escaping-symlink", "deep-escaping-symlink", "write-through-link",
        "link-through-link", "link-chain"])
def test_attacks_are_refused_and_nothing_is_written(tmp_path, extraction, members):
    tar = make_tar(tmp_path / "bad.tgz", members)
    dest = tmp_path / "out"
    with pytest.raises(UnsafeArchiveError):
        safe_extract(tar, dest)
    assert list(dest.iterdir()) == []
    assert not (tmp_path / "evil").exists() and not (tmp_path / "outside").exists()


def test_existing_link_in_dest_pointing_outside_is_not_followed(tmp_path, extraction):
    outside = tmp_path / "outside"
    outside.mkdir()
    dest = tmp_path / "out"
    dest.mkdir()
    os.symlink(str(outside), str(dest / "notes"))
    tar = make_tar(tmp_path / "bad.tgz", [("notes/x.md", "file", "x", 0o644)])
    with pytest.raises(UnsafeArchiveError):
        safe_extract(tar, dest)
    assert list(outside.iterdir()) == []


def test_file_replaces_inside_link_instead_of_writing_through(tmp_path, extraction):
    dest = tmp_path / "out"
    dest.mkdir()
    (dest / "target.md").write_text("keep me\n")
    os.symlink("target.md", str(dest / "f.md"))
    tar = make_tar(tmp_path / "ok.tgz", [("f.md", "file", "new\n", 0o644)])
    safe_extract(tar, dest)
    assert not (dest / "f.md").is_symlink()
    assert (dest / "f.md").read_text() == "new\n"
    assert (dest / "target.md").read_text() == "keep me\n"


def test_setuid_and_world_write_bits_are_stripped(tmp_path, extraction):
    tar = make_tar(tmp_path / "modes.tgz", [("tool", "file", "x", 0o4777), ("doc", "file", "y", 0o666)])
    dest = tmp_path / "out"
    safe_extract(tar, dest)
    tool_mode = os.stat(dest / "tool").st_mode
    assert not tool_mode & stat.S_ISUID and tool_mode & 0o777 == 0o755
    assert os.stat(dest / "doc").st_mode & 0o022 == 0


def test_member_names_are_cleaned(tmp_path):
    tar = make_tar(tmp_path / "n.tgz", [("./a/", "dir", None, 0o755), ("./a//b.md", "file", "b", 0o644)])
    assert member_names(tar) == ["a", "a/b.md"]


def make_zip(path, entries):
    """entries: (name, payload, unix_mode or None, is_symlink)."""
    with zipfile.ZipFile(str(path), "w") as archive:
        for name, payload, mode, is_link in entries:
            info = zipfile.ZipInfo(name)
            if mode is not None:
                info.external_attr = ((0o120000 if is_link else 0o100000) | mode) << 16
            archive.writestr(info, payload)
    return path


def test_unzip_extracts_and_keeps_exec_bit(tmp_path):
    archive = make_zip(tmp_path / "ok.zip", [("pkg/gws", "#!/bin/sh\n", 0o755, False),
                                              ("pkg/README", "hi", 0o644, False)])
    dest = tmp_path / "out"
    assert safe_unzip(archive, dest) == ["pkg/gws", "pkg/README"]
    assert os.stat(dest / "pkg/gws").st_mode & 0o777 == 0o755
    assert (dest / "pkg/README").read_text() == "hi"


@pytest.mark.parametrize("entry", [
    ("../evil", "x", 0o644, False),
    ("/abs/evil", "x", 0o644, False),
    ("link", "/etc/passwd", 0o777, True),
], ids=["dotdot", "absolute", "symlink"])
def test_unzip_attacks_are_refused(tmp_path, entry):
    archive = make_zip(tmp_path / "bad.zip", [("ok", "x", 0o644, False), entry])
    dest = tmp_path / "out"
    with pytest.raises(UnsafeArchiveError):
        safe_unzip(archive, dest)
    assert list(dest.iterdir()) == []
