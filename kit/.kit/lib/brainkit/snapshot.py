"""Safety copies of the workspace taken before an update, and putting them back.

A snapshot is a .tgz of the whole workspace except bulky or generated areas, readable by the
owner only (it holds briefs, packets and the Slack ledger). Names carry a UTC timestamp,
`20261013T155401Z-v0.1.0-to-v0.2.0.tgz`, so "newest" survives clock changes. Two ways back:

- after a failed update (automatic, seconds later): every snapshotted path is restored exactly
  and the files the failed update created are removed;
- `brain rollback` (by hand, maybe days later): only kit files, their `.new` suggestions and kit
  state come back. The user's own files are never touched, not even ones deleted or moved since.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import tarfile
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from . import manifest as manifest_mod
from . import safe_tar
from .apply import (INCOMING_STATE, MIGRATIONS_STATE, NEW_SUFFIX, SKILL_LINK_PREFIX,
                    load_incoming, prune_empty_dirs, rebuild_links, sync_codex_rules)
from .paths import Workspace

EXCLUDE = {".kit/snapshots", ".kit/cache", ".kit/run.lock", "reports", "logs"}
SKIP_DIR_NAMES = {"__pycache__"}
KEEP = 3
SUFFIX = ".tgz"
KIT_STATE = (".kit/manifest.json", f".kit/state/{MIGRATIONS_STATE}", f".kit/state/{INCOMING_STATE}")
_UTC_FORMAT = "%Y%m%dT%H%M%SZ"
_NAME_RE = re.compile(r"^(\d{8}T\d{6}Z)-.+\.tgz$")
_OLD_NAME_RE = re.compile(r"^(\d{8}-\d{6})-.+\.tgz$")  # before UTC names: local clock time


@dataclass
class RestoreReport:
    snapshot: str
    restored: int = 0
    removed: List[str] = field(default_factory=list)
    kept_modified: List[str] = field(default_factory=list)  # snapshot copy written as <file>.new
    kept_edited: List[str] = field(default_factory=list)  # newer kit files the user edited, left alone


# --- taking snapshots ---------------------------------------------------------------------

def _entries(ws: Workspace) -> List[Tuple[str, str, str]]:
    """(relpath, kind, link target) for everything worth saving, in a stable order."""
    root = str(ws.root)
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root)
        rel_dir = "" if rel_dir == "." else rel_dir.replace(os.sep, "/")
        keep_dirs = []
        for name in sorted(dirnames):
            rel = f"{rel_dir}/{name}" if rel_dir else name
            full = os.path.join(dirpath, name)
            if rel in EXCLUDE or name in SKIP_DIR_NAMES:
                continue
            if os.path.islink(full):
                out.append((rel, "link", os.readlink(full)))
                continue
            out.append((rel, "dir", ""))
            keep_dirs.append(name)
        dirnames[:] = keep_dirs
        for name in sorted(filenames):
            rel = f"{rel_dir}/{name}" if rel_dir else name
            full = os.path.join(dirpath, name)
            if rel in EXCLUDE:
                continue
            if os.path.islink(full):
                out.append((rel, "link", os.readlink(full)))
            elif os.path.isfile(full):
                out.append((rel, "file", ""))
    return _restorable(out, ws.root)


def _restorable(entries: List[Tuple[str, str, str]], root: Path) -> List[Tuple[str, str, str]]:
    """Drop links that point outside the workspace: they could not be restored safely."""
    links = {rel for rel, kind, _ in entries if kind == "link"}
    root_real = os.path.realpath(str(root))
    kept = []
    for rel, kind, target in entries:
        if kind == "link":
            try:
                safe_tar.check_link(rel, target, links, root_real)
            except safe_tar.UnsafeArchiveError:
                continue
        kept.append((rel, kind, target))
    return kept


def _utc(when: Optional[datetime]) -> datetime:
    """Aware times are converted; naive ones are taken as UTC already."""
    if when is None:
        return datetime.now(timezone.utc)
    if when.tzinfo is None:
        return when.replace(tzinfo=timezone.utc)
    return when.astimezone(timezone.utc)


def _unique_path(folder: Path, label: str, when: datetime) -> Path:
    stem = f"{when.strftime(_UTC_FORMAT)}-{label}"
    path, n = folder / f"{stem}{SUFFIX}", 1
    while path.exists():
        n += 1
        path = folder / f"{stem}-{n}{SUFFIX}"
    return path


def _private_folder(ws: Workspace) -> Path:
    ws.snapshots.mkdir(parents=True, exist_ok=True)
    os.chmod(str(ws.snapshots), 0o700)
    for path in list_snapshots(ws):  # copies made before snapshots were private
        try:
            os.chmod(str(path), 0o600)
        except OSError:
            pass
    return ws.snapshots


def create(ws: Workspace, label: str, when: Optional[datetime] = None, keep: int = KEEP) -> Path:
    """Write `.kit/snapshots/<UTC stamp>-<label>.tgz` (mode 0600) and keep only the newest `keep`."""
    folder = _private_folder(ws)
    final = _unique_path(folder, re.sub(r"[^A-Za-z0-9._-]", "_", label), _utc(when))
    part = final.with_name(final.name + ".part")
    try:
        fd = os.open(str(part), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.fchmod(fd, 0o600)  # whatever the umask says
        with os.fdopen(fd, "wb") as raw:
            with tarfile.open(fileobj=raw, mode="w:gz", format=tarfile.PAX_FORMAT) as archive:
                for rel, kind, target in _entries(ws):
                    _add(archive, ws.root / rel, rel, kind, target)
        os.replace(str(part), str(final))
    except BaseException:
        try:
            part.unlink()
        except OSError:
            pass
        raise
    prune(ws, keep, protect={final.name})
    return final


def _add(archive: tarfile.TarFile, full: Path, rel: str, kind: str, target: str) -> None:
    try:
        st = os.lstat(str(full))
    except FileNotFoundError:
        return  # vanished while we were walking
    info = tarfile.TarInfo(rel)
    info.mtime = int(st.st_mtime)
    info.mode = stat.S_IMODE(st.st_mode)
    if kind == "dir":
        info.type = tarfile.DIRTYPE
        archive.addfile(info)
    elif kind == "link":
        info.type, info.linkname = tarfile.SYMTYPE, target
        archive.addfile(info)
    else:
        try:
            handle = open(str(full), "rb")
        except (FileNotFoundError, PermissionError):
            return  # unreadable to us means untouchable by the update too: skip, don't block
        with handle:
            info.size = os.fstat(handle.fileno()).st_size
            archive.addfile(info, handle)


def snapshot_time(name: str) -> Optional[float]:
    """Seconds since the epoch from a snapshot name (UTC names; older local-time names too)."""
    match = _NAME_RE.match(name)
    if match:
        moment = datetime.strptime(match.group(1), _UTC_FORMAT).replace(tzinfo=timezone.utc)
        return moment.timestamp()
    match = _OLD_NAME_RE.match(name)
    if match:
        return datetime.strptime(match.group(1), "%Y%m%d-%H%M%S").timestamp()  # local time
    return None


def list_snapshots(ws: Workspace) -> List[Path]:
    """Snapshots, newest first."""
    if not ws.snapshots.is_dir():
        return []
    found = []
    for path in ws.snapshots.iterdir():
        stamp = snapshot_time(path.name)
        if stamp is not None and path.is_file():
            found.append((stamp, path.stat().st_mtime_ns, path.name, path))
    return [item[3] for item in sorted(found, reverse=True)]


def prune(ws: Workspace, keep: int = KEEP, protect: Iterable[str] = ()) -> List[str]:
    protected = set(protect)
    removed = []
    for path in list_snapshots(ws)[keep:]:
        if path.name in protected:
            continue
        try:
            path.unlink()
            removed.append(path.name)
        except OSError:
            pass
    return removed


# --- putting them back --------------------------------------------------------------------

def _manifest_or_empty(path: Path) -> Dict[str, Any]:
    try:
        return manifest_mod.validate(manifest_mod.load_optional(path) or manifest_mod.empty())
    except (OSError, ValueError):
        return manifest_mod.empty()


def _remove_if_known(ws: Workspace, rel: str, hashes: Set[str], keep_dirs: Iterable[str],
                     report: RestoreReport) -> None:
    """Delete `rel` only while it still holds one of these exact kit contents."""
    path = ws.root / rel
    if path.is_file() and not path.is_symlink() and manifest_mod.hash_file(path) in hashes:
        path.unlink()
        prune_empty_dirs(path.parent, ws.root, keep=frozenset(keep_dirs))
        report.removed.append(rel)


def _drop_unsaved_state(ws: Workspace, saved: Iterable[str], report: RestoreReport) -> None:
    """Kit state the snapshot didn't have (e.g. migration records) must not outlive a rollback."""
    names = set(saved)
    for rel in KIT_STATE:
        path = ws.root / rel
        if rel not in names and path.is_file() and not path.is_symlink():
            path.unlink()
            report.removed.append(rel)


def _drop_new_skill_links(ws: Workspace, saved: Set[str]) -> None:
    """Skill links the failed apply made. The user's links elsewhere are never touched."""
    links = ws.agents_skills
    if not links.is_dir() or links.is_symlink():
        return
    for entry in sorted(os.listdir(str(links))):
        path = links / entry
        if (path.is_symlink() and f".agents/skills/{entry}" not in saved
                and os.readlink(str(path)).startswith(SKILL_LINK_PREFIX)):
            path.unlink()


def restore_after_failed_update(ws: Workspace, snapshot: Path, new_manifest: Dict[str, Any],
                                release_dir: Optional[Path] = None, home: Optional[Path] = None,
                                log=None) -> RestoreReport:
    """Exact restore: remove what the failed update wrote, then put every saved path back.

    "What it wrote" = any kit file of the new release (and its `.new`) that the snapshot doesn't
    have and that still holds exactly the release's bytes; same for new templates. That covers
    the kit files new in this release, and never touches anything the user wrote.
    """
    names = set(safe_tar.member_names(snapshot))
    report = RestoreReport(snapshot=snapshot.name)
    new_kit = new_manifest.get("kit_files", {})
    for rel in sorted(new_kit):
        for candidate in (rel, rel + NEW_SUFFIX):
            if candidate not in names:
                _remove_if_known(ws, candidate, {new_kit[rel]["sha256"]}, names, report)
    if release_dir is not None:
        for rel in new_manifest.get("templates", []):
            src, dst = Path(release_dir) / "templates" / rel, ws.root / rel
            if (rel not in names and dst.is_file() and not dst.is_symlink() and src.is_file()
                    and dst.read_bytes() == src.read_bytes()):
                dst.unlink()
                prune_empty_dirs(dst.parent, ws.root, keep=frozenset(names))
                report.removed.append(rel)
    _drop_unsaved_state(ws, names, report)
    _drop_new_skill_links(ws, names)
    report.restored = len(safe_tar.safe_extract(snapshot, ws.root, keep_modes=True))
    sync_codex_rules(ws, home if home is not None else Path.home())
    if log is not None:
        log.info("restored %s (%d paths, %d removed)", snapshot.name, report.restored,
                 len(report.removed))
    return report


def _inventory(root: Path) -> Dict[str, str]:
    out = {}
    for dirpath, dirnames, filenames in os.walk(str(root)):
        for name in dirnames + filenames:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, str(root)).replace(os.sep, "/")
            if os.path.islink(full):
                out[rel] = "link"
            elif os.path.isdir(full):
                out[rel] = "dir"
            elif os.path.isfile(full):
                out[rel] = "file"
    return out


def _copy_file(src: Path, dst: Path) -> bool:
    """Put one file back exactly (bytes, mode, mtime). Never over a folder."""
    if dst.is_dir() and not dst.is_symlink():
        return False
    st = src.stat()
    manifest_mod.atomic_write(dst, src.read_bytes(), stat.S_IMODE(st.st_mode))
    os.utime(str(dst), (st.st_atime, st.st_mtime))
    return True


def _users_edit(dst: Path, src: Path, known: Set[str]) -> bool:
    """True when `dst` holds something other than kit content (the user's edit, or not a file)."""
    if not os.path.lexists(str(dst)):
        return False
    if dst.is_symlink() or not dst.is_file():
        return True
    now = manifest_mod.hash_file(dst)
    return now not in known and now != manifest_mod.hash_file(src)


def restore_by_hand(ws: Workspace, snapshot: Path, home: Optional[Path] = None,
                    log=None) -> RestoreReport:
    """`brain rollback`: kit files, their `.new` suggestions and kit state from the snapshot.

    The user's own paths are never touched: files created, deleted or moved since stay that way.
    A kit path counts as unedited when it holds the content of the installed release, of an
    update that was cut off half-way, or of the snapshot. Anything else is the user's edit and
    stays, with the snapshot's version written beside it as `<file>.new`.
    """
    report = RestoreReport(snapshot=snapshot.name)
    installed = _manifest_or_empty(ws.manifest)
    unfinished = load_incoming(ws)
    cache = ws.kit / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="rollback-", dir=str(cache)))
    try:
        safe_tar.safe_extract(snapshot, tmp, keep_modes=True)
        saved = _inventory(tmp)
        saved_manifest = _manifest_or_empty(tmp / ".kit" / "manifest.json")
        later = manifest_mod.kit_hashes(installed, unfinished)
        known = manifest_mod.kit_hashes(installed, unfinished, saved_manifest)
        # 1. kit files (and .new suggestions) from after the snapshot go, unless edited since
        for rel in sorted(later):
            for candidate in (rel, rel + NEW_SUFFIX):
                if candidate not in saved:
                    _remove_if_known(ws, candidate, known[rel], saved, report)
            if (rel not in saved and rel not in report.removed
                    and os.path.lexists(str(ws.root / rel))):
                report.kept_edited.append(rel)
        # 2. kit state exactly as it was
        _drop_unsaved_state(ws, saved, report)
        for rel in KIT_STATE:
            if saved.get(rel) == "file" and _copy_file(tmp / rel, ws.root / rel):
                report.restored += 1
        # 3. the snapshot's kit files and their .new suggestions
        for rel in sorted(saved_manifest["kit_files"]):
            hashes = known.get(rel, set())
            if saved.get(rel) == "file":
                src, dst = tmp / rel, ws.root / rel
                if _users_edit(dst, src, hashes):
                    _copy_file(src, dst.with_name(dst.name + NEW_SUFFIX))
                    report.kept_modified.append(rel)
                elif _copy_file(src, dst):
                    report.restored += 1
            suggestion = rel + NEW_SUFFIX
            if saved.get(suggestion) == "file":
                src, dst = tmp / suggestion, ws.root / suggestion
                if not _users_edit(dst, src, hashes) and _copy_file(src, dst):
                    report.restored += 1
        rebuild_links(ws)
        sync_codex_rules(ws, home if home is not None else Path.home())
    finally:
        shutil.rmtree(str(tmp), ignore_errors=True)
    if log is not None:
        log.info("rolled back to %s (%d restored, %d removed)", snapshot.name, report.restored,
                 len(report.removed))
    return report
