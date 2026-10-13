"""Safe archive extraction: refuse anything that could write outside the destination.

Every member is checked before anything is written, so a refused archive leaves no trace.
Refused: absolute paths, `..` components, hard links, device files and FIFOs, symlinks to
absolute paths or out of the destination, symlinks that pass through other symlinks, and any
path that would be written through a symlink. Apple's Python 3.9 may predate
`tarfile.data_filter`; when it exists it is used as a second line of defence.
"""

from __future__ import annotations

import copy
import os
import re
import shutil
import tarfile
import warnings
import zipfile
from pathlib import Path
from typing import Iterable, List, Set, Tuple

_HAS_DATA_FILTER = hasattr(tarfile, "data_filter")
_FILTER_ERROR = getattr(tarfile, "FilterError", None)
_DRIVE = re.compile(r"^[A-Za-z]:")


class UnsafeArchiveError(ValueError):
    pass


def clean_name(name: str) -> str:
    """'./a//b/' -> 'a/b'. Raises for absolute paths and '..' components."""
    if not isinstance(name, str) or "\x00" in name:
        raise UnsafeArchiveError(f"bad name in archive: {name!r}")
    if name.startswith(("/", "\\")) or _DRIVE.match(name):
        raise UnsafeArchiveError(f"absolute path in archive: {name}")
    parts = [p for p in name.split("/") if p not in ("", ".")]
    if ".." in parts:
        raise UnsafeArchiveError(f"'..' in archive path: {name}")
    return "/".join(parts)


def _within(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def check_link(name: str, target: str, links: Set[str], dest_real: str) -> None:
    """A symlink `name -> target` must stay inside dest without passing through other links."""
    if not target or target.startswith(("/", "\\")) or _DRIVE.match(target):
        raise UnsafeArchiveError(f"link to an absolute path: {name} -> {target}")
    stack = name.split("/")[:-1]
    comps = [c for c in target.split("/") if c not in ("", ".")]
    for index, comp in enumerate(comps):
        if comp == "..":
            if not stack:
                raise UnsafeArchiveError(f"link points outside: {name} -> {target}")
            stack.pop()
            continue
        stack.append(comp)
        if index < len(comps) - 1 and "/".join(stack) in links:
            raise UnsafeArchiveError(f"link passes through another link: {name} -> {target}")
    parent = os.path.join(dest_real, *name.split("/")[:-1])
    if not _within(os.path.realpath(os.path.join(parent, target)), dest_real):
        raise UnsafeArchiveError(f"link points outside: {name} -> {target}")


def _check_path(name: str, kind: str, links: Set[str], dest_real: str) -> None:
    parts = name.split("/")
    for i in range(1, len(parts)):
        if "/".join(parts[:i]) in links:
            raise UnsafeArchiveError(f"archive path goes through a link: {name}")
    parent = os.path.realpath(os.path.join(dest_real, *parts[:-1]))
    if not _within(parent, dest_real):
        raise UnsafeArchiveError(f"archive path leads outside: {name}")
    if kind != "link":
        full = os.path.realpath(os.path.join(dest_real, *parts))
        if not _within(full, dest_real):
            raise UnsafeArchiveError(f"archive path leads outside: {name}")


def check_entries(entries: Iterable[Tuple[str, str, str]], dest) -> List[str]:
    """Validate (name, kind, link_target) entries; kind is 'file', 'dir' or 'link'.

    Returns the cleaned names in order. Raises UnsafeArchiveError on the first problem.
    """
    dest_real = os.path.realpath(str(dest))
    cleaned = []
    for name, kind, target in entries:
        clean = clean_name(name)
        if not clean and kind != "dir":
            raise UnsafeArchiveError(f"bad name in archive: {name!r}")
        cleaned.append((clean, kind, target))
    links = {clean for clean, kind, _ in cleaned if kind == "link"}
    for clean, kind, target in cleaned:
        if not clean:
            continue
        _check_path(clean, kind, links, dest_real)
        if kind == "link":
            check_link(clean, target, links, dest_real)
    return [clean for clean, _, _ in cleaned]


def _kind(member: tarfile.TarInfo) -> str:
    if member.islnk():
        raise UnsafeArchiveError(f"hard link not allowed: {member.name}")
    if member.issym():
        return "link"
    if member.isdir():
        return "dir"
    if member.isreg():
        return "file"
    raise UnsafeArchiveError(f"special file not allowed: {member.name}")


def _sanitized(member: tarfile.TarInfo) -> tarfile.TarInfo:
    """Copy with sane modes (no setuid/sticky, no group/other write) for Pythons without filters."""
    out = copy.copy(member)
    if member.isdir():
        out.mode = (member.mode & 0o755) | 0o700
    elif member.isreg():
        mode = member.mode & 0o755
        if not mode & 0o100:
            mode &= ~0o111
        out.mode = mode | 0o600
    return out


def member_names(tar_path) -> List[str]:
    """Cleaned names of every member (validated against an empty destination)."""
    with tarfile.open(str(tar_path), "r:*") as archive:
        members = archive.getmembers()
    entries = [(m.name, _kind(m), m.linkname) for m in members]
    return [n for n in check_entries(entries, os.sep + "nonexistent-brain-dest") if n]


def safe_extract(tar_path, dest, keep_modes: bool = False) -> List[str]:
    """Extract a tar (any compression) into `dest`. Returns the cleaned member names.

    keep_modes: put back each file's own permission bits (minus setuid/setgid/sticky) after the
    filters normalised them. Only for our own snapshots, where "exactly as it was" matters.
    """
    target = Path(dest)
    target.mkdir(parents=True, exist_ok=True)
    with tarfile.open(str(tar_path), "r:*") as archive:
        members = archive.getmembers()
        names = check_entries([(m.name, _kind(m), m.linkname) for m in members], target)
        for name, member in zip(names, members):
            path = target.joinpath(*name.split("/")) if name else target
            if member.isreg() and path.is_symlink():
                path.unlink()  # replace a link with the file, never write through it
        try:
            if _HAS_DATA_FILTER:
                archive.extractall(str(target), members=members, filter="data")
            else:
                with warnings.catch_warnings():  # members are already checked and sanitized
                    warnings.simplefilter("ignore", DeprecationWarning)
                    archive.extractall(str(target), members=[_sanitized(m) for m in members])
        except Exception as exc:
            if _FILTER_ERROR is not None and isinstance(exc, _FILTER_ERROR):
                raise UnsafeArchiveError(str(exc)) from exc
            raise
        if keep_modes:
            for name, member in zip(names, members):
                if name and member.isreg():
                    os.chmod(str(target.joinpath(*name.split("/"))), member.mode & 0o777)
    return [n for n in names if n]


def _zip_kind(info: zipfile.ZipInfo) -> str:
    kind = (info.external_attr >> 16) & 0o170000
    if kind == 0o120000:
        raise UnsafeArchiveError(f"link not allowed in zip: {info.filename}")
    if kind not in (0, 0o100000, 0o040000):
        raise UnsafeArchiveError(f"special file not allowed in zip: {info.filename}")
    return "dir" if info.is_dir() or kind == 0o040000 else "file"


def safe_unzip(zip_path, dest) -> List[str]:
    """Extract a zip into `dest` with the same checks (zip symlinks are refused)."""
    target = Path(dest)
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(str(zip_path)) as archive:
        infos = archive.infolist()
        kinds = [_zip_kind(info) for info in infos]
        names = check_entries([(i.filename, k, "") for i, k in zip(infos, kinds)], target)
        for info, kind, name in zip(infos, kinds, names):
            if not name:
                continue
            path = target.joinpath(*name.split("/"))
            if kind == "dir":
                path.mkdir(parents=True, exist_ok=True)
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.is_symlink():
                path.unlink()
            with archive.open(info) as src, open(str(path), "wb") as out:
                shutil.copyfileobj(src, out)
            executable = (info.external_attr >> 16) & 0o100
            os.chmod(str(path), 0o755 if executable else 0o644)
    return [n for n in names if n]
