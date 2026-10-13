"""Release manifests: which files are kit-owned, with their hashes and modes.

The manifest is how an update tells the user's edits apart from ours: a kit file whose hash still
matches the installed manifest is unedited and safe to replace. Shape:

    {"name": "brain-starter", "version": "0.3.1",
     "kit_files": {"AGENTS.md": {"sha256": "…", "mode": "0644"}, …},
     "templates": ["me.md", …], "migrations": ["0002_rename_x", …]}
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

NAME = "brain-starter"
SKIP_DIRS = {"__pycache__"}
SKIP_FILES = {".DS_Store"}
SKIP_SUFFIXES = (".pyc",)
EXEC_PREFIXES = (".kit/bin/",)  # entry points are always executable, whatever the checkout says
MIGRATION_FILE_RE = re.compile(r"^(\d{4}_[A-Za-z0-9_]+)\.py$")
MIGRATION_ID_RE = re.compile(r"^\d{4}_[A-Za-z0-9_]+$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")


class ManifestError(ValueError):
    pass


def hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def hash_file(path) -> str:
    digest = hashlib.sha256()
    with open(str(path), "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_junk(name: str) -> bool:
    return name in SKIP_FILES or name.endswith(SKIP_SUFFIXES)


def iter_files(root) -> List[str]:
    """Sorted posix paths (relative to `root`) of the regular files under it.

    Caches and Finder junk are skipped. Symlinks are refused: a release ships plain files only.
    """
    base = Path(root)
    if not base.is_dir():
        return []
    out = []
    for dirpath, dirnames, filenames in os.walk(str(base)):
        for name in dirnames:
            if os.path.islink(os.path.join(dirpath, name)):
                raise ManifestError(f"symlink not allowed in a release: {_rel(base, dirpath, name)}")
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in filenames:
            if is_junk(name):
                continue
            full = os.path.join(dirpath, name)
            if os.path.islink(full):
                raise ManifestError(f"symlink not allowed in a release: {_rel(base, dirpath, name)}")
            if os.path.isfile(full):
                out.append(_rel(base, dirpath, name))
    return sorted(out)


def _rel(base: Path, dirpath: str, name: str) -> str:
    return Path(dirpath, name).relative_to(base).as_posix()


def file_mode(path, rel: str = "") -> str:
    if rel.startswith(EXEC_PREFIXES):
        return "0755"
    return "0755" if os.stat(str(path)).st_mode & 0o111 else "0644"


def mode_bits(mode: str) -> int:
    return int(mode, 8)


def migration_ids(directory) -> List[str]:
    folder = Path(directory)
    if not folder.is_dir():
        return []
    ids = []
    for name in sorted(os.listdir(str(folder))):
        match = MIGRATION_FILE_RE.match(name)
        if match and (folder / name).is_file():
            ids.append(match.group(1))
    return ids


def build_manifest(release_root) -> Dict[str, Any]:
    """Manifest for a release tree that has kit/, templates/ and migrations/."""
    root = Path(release_root)
    kit = root / "kit"
    try:
        version = (kit / ".kit" / "VERSION").read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ManifestError(f"no kit/.kit/VERSION in {root}") from exc
    if not version:
        raise ManifestError("kit/.kit/VERSION is empty")
    kit_files = {}
    for rel in iter_files(kit):
        full = kit / rel
        kit_files[rel] = {"sha256": hash_file(full), "mode": file_mode(full, rel)}
    return {
        "name": NAME,
        "version": version,
        "kit_files": kit_files,
        "templates": iter_files(root / "templates"),
        "migrations": migration_ids(root / "migrations"),
    }


def empty() -> Dict[str, Any]:
    """Stands in for 'nothing installed yet'."""
    return {"name": NAME, "version": "0.0.0", "kit_files": {}, "templates": [], "migrations": []}


def kit_hashes(*manifests: Optional[Dict[str, Any]]) -> Dict[str, Set[str]]:
    """{kit path: every hash any of these manifests gives it} (None entries are skipped).

    A file whose hash is in this set holds kit content from one of those releases, so the
    user hasn't edited it.
    """
    out: Dict[str, Set[str]] = {}
    for data in manifests:
        for rel, meta in ((data or {}).get("kit_files") or {}).items():
            out.setdefault(rel, set()).add(meta["sha256"])
    return out


def check_relpath(rel: str) -> str:
    """Refuse a path that could point outside the workspace."""
    if not isinstance(rel, str) or not rel or rel.startswith("/") or "\x00" in rel:
        raise ManifestError(f"bad path in manifest: {rel!r}")
    if any(part in ("", ".", "..") for part in rel.split("/")):
        raise ManifestError(f"bad path in manifest: {rel!r}")
    return rel


def validate(data: Any) -> Dict[str, Any]:
    """Check the shape of a manifest (and that no path escapes). Returns it unchanged."""
    if not isinstance(data, dict) or data.get("name") != NAME:
        raise ManifestError("not a brain-starter manifest")
    if not isinstance(data.get("version"), str) or not data["version"]:
        raise ManifestError("manifest has no version")
    kit_files = data.get("kit_files")
    if not isinstance(kit_files, dict):
        raise ManifestError("manifest has no kit_files")
    for rel, meta in kit_files.items():
        check_relpath(rel)
        if not isinstance(meta, dict) or not _SHA_RE.match(str(meta.get("sha256", ""))):
            raise ManifestError(f"bad hash for {rel}")
        if meta.get("mode") not in ("0755", "0644"):
            raise ManifestError(f"bad mode for {rel}")
    templates = data.get("templates", [])
    if not isinstance(templates, list):
        raise ManifestError("manifest templates must be a list")
    for rel in templates:
        check_relpath(rel)
    migrations = data.get("migrations", [])
    if not isinstance(migrations, list) or not all(
            isinstance(m, str) and MIGRATION_ID_RE.match(m) for m in migrations):
        raise ManifestError("bad migrations list in manifest")
    return data


def load(path) -> Dict[str, Any]:
    with open(str(path), encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ManifestError(f"{path} is not a JSON object")
    return data


def load_optional(path) -> Optional[Dict[str, Any]]:
    """The manifest, or None when the file does not exist."""
    try:
        return load(path)
    except FileNotFoundError:
        return None


def dumps(data: Dict[str, Any]) -> str:
    return json.dumps(data, indent=2, sort_keys=True) + "\n"


def save(path, data: Dict[str, Any]) -> None:
    atomic_write(path, dumps(data).encode("utf-8"), 0o644)


def atomic_write(path, data: bytes, mode: int = 0o644) -> None:
    """Write via a temp file in the same folder + rename, so readers never see half a file."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, str(target))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
