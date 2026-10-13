#!/usr/bin/env python3
"""Build a release: dist/brain-starter-vX.Y.Z.tar.gz, dist/install.sh and dist/SHA256SUMS.

Usage: python3 tools/build_release.py [--version X.Y.Z] [--out dist]

The tarball has one top folder, brain-starter-vX.Y.Z/, holding kit/, templates/, migrations/ and
manifest.json. Output is byte-for-byte reproducible: sorted entries, fixed mtime, root owner,
no user names, modes normalised to 0755/0644, gzip header without name or time.
"""

from __future__ import annotations

import argparse
import ast
import gzip
import hashlib
import io
import os
import re
import shutil
import sys
import tarfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "kit" / ".kit" / "lib"))

from brainkit import manifest as manifest_mod  # noqa: E402

FIXED_MTIME = 1767225600  # 2026-01-01T00:00:00Z
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
# (path in archive, source, mode, expected sha256 or ""); source None = folder
Entry = Tuple[str, Union[Path, bytes, None], int, str]


class BuildError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _check_migrations(root: Path) -> None:
    folder = root / "migrations"
    for rel in manifest_mod.iter_files(folder):
        if not rel.endswith(".py"):
            continue
        name = rel.rsplit("/", 1)[-1]
        if "/" in rel or not manifest_mod.MIGRATION_FILE_RE.match(name):
            raise BuildError(f"migrations/{rel}: name must look like 0002_short_name.py")
        tree = ast.parse((folder / rel).read_text(encoding="utf-8"), filename=rel)
        if not any(isinstance(node, ast.FunctionDef) and node.name == "migrate"
                   for node in tree.body):
            raise BuildError(f"migrations/{rel}: needs a top-level migrate(workspace)")


def _entries(root: Path, data: Dict) -> List[Entry]:
    files: List[Entry] = []
    for rel, meta in data["kit_files"].items():
        files.append((f"kit/{rel}", root / "kit" / rel, manifest_mod.mode_bits(meta["mode"]),
                      meta["sha256"]))
    for rel in data["templates"]:
        src = root / "templates" / rel
        mode = manifest_mod.mode_bits(manifest_mod.file_mode(src))
        files.append((f"templates/{rel}", src, mode, ""))
    for rel in manifest_mod.iter_files(root / "migrations"):
        files.append((f"migrations/{rel}", root / "migrations" / rel, 0o644, ""))
    files.append(("manifest.json", manifest_mod.dumps(data).encode("utf-8"), 0o644, ""))
    folders = set()
    for arcname, _, _, _ in files:
        parts = arcname.split("/")[:-1]
        for i in range(1, len(parts) + 1):
            folders.add("/".join(parts[:i]))
    entries: List[Entry] = [(f, None, 0o755, "") for f in folders] + files
    return sorted(entries, key=lambda e: e[0])


def _write_tarball(path: Path, top: str, entries: List[Entry]) -> None:
    partial = path.with_name(path.name + ".part")
    try:
        _write_entries(partial, top, entries)
    except BaseException:
        if partial.exists():
            partial.unlink()
        raise
    os.replace(str(partial), str(path))


def _write_entries(partial: Path, top: str, entries: List[Entry]) -> None:
    with open(str(partial), "wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=FIXED_MTIME,
                           compresslevel=9) as gz:
            with tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as archive:
                for arcname, source, mode, sha in [("", None, 0o755, "")] + entries:
                    info = tarfile.TarInfo(f"{top}/{arcname}" if arcname else top)
                    info.mtime, info.mode = FIXED_MTIME, mode
                    info.uid = info.gid = 0
                    info.uname = info.gname = ""
                    if source is None:
                        info.type = tarfile.DIRTYPE
                        archive.addfile(info)
                        continue
                    data = source if isinstance(source, bytes) else source.read_bytes()
                    if sha and manifest_mod.hash_bytes(data) != sha:
                        raise BuildError(f"{arcname} changed during the build; run it again")
                    info.size = len(data)
                    archive.addfile(info, io.BytesIO(data))


def build(root: Path, out: Path, version: Optional[str] = None) -> Dict[str, Path]:
    root = Path(root).resolve()
    try:
        kit_version = (root / "kit" / ".kit" / "VERSION").read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise BuildError(f"no kit/.kit/VERSION under {root}") from exc
    if not VERSION_RE.match(kit_version):
        raise BuildError(f"kit/.kit/VERSION must look like 1.2.3, not {kit_version!r}")
    if version is not None and version.lstrip("vV") != kit_version:
        raise BuildError(f"--version {version} disagrees with kit/.kit/VERSION ({kit_version})")
    install_sh = root / "install.sh"
    if not install_sh.is_file():
        raise BuildError("install.sh is missing")
    if not (root / "kit" / ".kit" / "bin" / "brain").is_file():
        raise BuildError("kit/.kit/bin/brain is missing")
    data = manifest_mod.build_manifest(root)
    overlap = sorted(set(data["kit_files"]) & set(data["templates"]))
    if overlap:
        raise BuildError("both kit file and template: " + ", ".join(overlap))
    _check_migrations(root)

    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    top = f"brain-starter-v{kit_version}"
    tarball = out / f"{top}.tar.gz"
    _write_tarball(tarball, top, _entries(root, data))
    installer = out / "install.sh"
    shutil.copyfile(str(install_sh), str(installer))
    os.chmod(str(installer), 0o755)
    sums = out / "SHA256SUMS"
    sums.write_text("".join(f"{_sha256(p)}  {p.name}\n"
                            for p in sorted([tarball, installer], key=lambda p: p.name)),
                    encoding="utf-8")
    return {"tarball": tarball, "install_sh": installer, "sums": sums}


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Build a brain-starter release into dist/.")
    parser.add_argument("--version", help="must match kit/.kit/VERSION")
    parser.add_argument("--out", default=str(REPO / "dist"), help="output folder (default: dist/)")
    parser.add_argument("--root", default=str(REPO), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        paths = build(Path(args.root), Path(args.out), args.version)
    except (BuildError, manifest_mod.ManifestError, OSError, SyntaxError) as exc:
        print(f"build failed: {exc}", file=sys.stderr)
        return 2
    for key in ("tarball", "install_sh", "sums"):
        print(paths[key])
    return 0


if __name__ == "__main__":
    sys.exit(main())
