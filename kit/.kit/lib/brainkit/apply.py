"""`brain _apply`: lay a release directory over a workspace without touching the user's files.

- A kit file still matching the installed manifest is unedited, so it is replaced.
- An edited kit file is kept; ours is written beside it as `<file>.new`.
- Kit files the release dropped are deleted only when unedited.
- Templates are created only when missing, never overwritten.
- `.agents/skills/<name>` links are rebuilt for every skill folder.
- Pending migrations run (update) or are marked done (fresh install).
- The Codex rules file is copied to ~/.codex/rules/ when the kit ships one.
- The new manifest is written last.

Before the first file changes, the incoming manifest is saved as
`.kit/state/incoming-manifest.json`; it is removed once the new manifest is in place. If it is
still there, an apply was cut off half-way: its files count as kit content (not the user's
edits) for the next apply and for `brain rollback`.

Contract: older kits run the NEW release's applier as
`brain _apply --release-dir D --workspace W --mode update --json`. Keep accepting exactly these
flags; HOME comes from the environment.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from . import manifest as manifest_mod
from .paths import Workspace

NEW_SUFFIX = ".new"
MIGRATIONS_STATE = "migrations.json"
INCOMING_STATE = "incoming-manifest.json"
SKILL_LINK_PREFIX = "../../.claude/skills/"
CODEX_RULES = ".kit/codex/brain-starter.rules"
VERSION_FILE = ".kit/VERSION"


class ApplyError(RuntimeError):
    pass


class MigrationError(ApplyError):
    pass


@dataclass
class ApplyReport:
    version: str = ""
    previous_version: str = ""
    added: List[str] = field(default_factory=list)
    replaced: List[str] = field(default_factory=list)
    unchanged: List[str] = field(default_factory=list)
    kept_modified: List[str] = field(default_factory=list)  # ours written beside as <file>.new
    removed: List[str] = field(default_factory=list)
    kept_removed_modified: List[str] = field(default_factory=list)
    templates_created: List[str] = field(default_factory=list)
    migrations_run: List[str] = field(default_factory=list)
    links: List[str] = field(default_factory=list)
    codex_rules: str = ""  # "", "written", "unchanged" or "failed: …"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ApplyReport":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in (data or {}).items() if k in known})

    def summary_lines(self) -> List[str]:
        lines = [f"{len(self.replaced)} kit files updated, {len(self.added)} added, "
                 f"{len(self.removed)} removed."]
        if self.templates_created:
            lines.append(f"{len(self.templates_created)} starter files created.")
        if self.migrations_run:
            lines.append("Ran: " + ", ".join(self.migrations_run))
        if self.kept_modified:
            lines.append("You had changed these kit files, so they were left alone. "
                         "The new version is beside each one:")
            lines.extend(f"  {rel}{NEW_SUFFIX}" for rel in self.kept_modified)
        if self.kept_removed_modified:
            lines.append("No longer part of the kit, kept because you changed them:")
            lines.extend(f"  {rel}" for rel in self.kept_removed_modified)
        if self.codex_rules.startswith("failed"):
            lines.append(f"Codex safety rules not copied ({self.codex_rules}).")
        return lines


def apply_release(release_dir, ws: Workspace, log, mode: str = "update",
                  home: Optional[Path] = None) -> ApplyReport:
    """Install or update `ws` from an extracted release. Raises ApplyError on failure."""
    if mode not in ("install", "update"):
        raise ValueError(f"unknown mode: {mode}")
    release = Path(release_dir)
    try:
        new = manifest_mod.validate(manifest_mod.load(release / "manifest.json"))
    except (OSError, ValueError) as exc:
        raise ApplyError(f"release has no usable manifest: {exc}") from exc
    _verify_release(release, new)
    old, fresh = _installed_manifest(ws, log)
    unfinished = load_incoming(ws)  # an earlier apply that was cut off half-way
    if unfinished is not None:
        log.warning("finishing an interrupted update to v%s", unfinished["version"])
    known = manifest_mod.kit_hashes(old, unfinished)

    report = ApplyReport(version=new["version"], previous_version=old.get("version", ""))
    ws.root.mkdir(parents=True, exist_ok=True)
    ws.ensure_dirs()
    try:
        manifest_mod.save(incoming_path(ws), new)  # before any kit file changes
        _apply_kit_files(release / "kit", ws.root, known, new["kit_files"], report, log)
        _remove_dropped(ws.root, known, new["kit_files"], report, log)
        _create_templates(release / "templates", ws.root, new["templates"], report)
    except OSError as exc:
        raise ApplyError(f"could not write kit files: {exc}") from exc
    report.links = rebuild_links(ws)
    report.codex_rules = sync_codex_rules(ws, home if home is not None else Path.home())
    if report.codex_rules.startswith("failed"):
        log.warning("codex rules: %s", report.codex_rules)
    _run_migrations(release / "migrations", ws, mode, fresh, report, log)
    manifest_mod.save(ws.manifest, new)
    _clear_incoming(ws)
    log.info("applied v%s (%s): %d added, %d replaced, %d kept, %d removed", new["version"], mode,
             len(report.added), len(report.replaced), len(report.kept_modified),
             len(report.removed))
    return report


def _verify_release(release: Path, new: Dict[str, Any]) -> None:
    """Every file the manifest promises is present, plain and intact before we write anything."""
    for rel, meta in new["kit_files"].items():
        src = release / "kit" / rel
        if src.is_symlink() or not src.is_file():
            raise ApplyError(f"release is missing kit/{rel}")
        if manifest_mod.hash_file(src) != meta["sha256"]:
            raise ApplyError(f"release file is damaged: kit/{rel}")
    for rel in new["templates"]:
        src = release / "templates" / rel
        if src.is_symlink() or not src.is_file():
            raise ApplyError(f"release is missing templates/{rel}")
    if set(new["kit_files"]) & set(new["templates"]):
        raise ApplyError("release lists a path as both kit file and template")


def incoming_path(ws: Workspace) -> Path:
    return ws.state / INCOMING_STATE


def load_incoming(ws: Workspace) -> Optional[Dict[str, Any]]:
    """The manifest of an apply that started but never finished, or None."""
    try:
        data = manifest_mod.load_optional(incoming_path(ws))
        return manifest_mod.validate(data) if data is not None else None
    except (OSError, ValueError):
        return None


def _clear_incoming(ws: Workspace) -> None:
    try:
        incoming_path(ws).unlink()
    except FileNotFoundError:
        pass


def _installed_manifest(ws: Workspace, log):
    """(manifest, fresh). A missing manifest means a fresh install; a broken one is ignored."""
    try:
        raw = manifest_mod.load_optional(ws.manifest)
    except (OSError, ValueError) as exc:
        log.warning("installed manifest unreadable (%s); treating kit files as edited", exc)
        return manifest_mod.empty(), False
    if raw is None:
        return manifest_mod.empty(), True
    try:
        return manifest_mod.validate(raw), False
    except ValueError as exc:
        log.warning("installed manifest invalid (%s); treating kit files as edited", exc)
        return manifest_mod.empty(), False


def _write_copy(src: Path, dst: Path, mode: int) -> None:
    manifest_mod.atomic_write(dst, src.read_bytes(), mode)


def _new_path(dst: Path) -> Path:
    return dst.with_name(dst.name + NEW_SUFFIX)


def _apply_kit_files(src_root: Path, root: Path, known: Dict[str, Set[str]],
                     new: Dict[str, Any], report: ApplyReport, log) -> None:
    """`known`: per path, the hashes of kit content already installed (or half-installed)."""
    # VERSION goes last: an interrupted apply must not claim the new version.
    for rel in sorted(new, key=lambda r: (r == VERSION_FILE, r)):
        meta = new[rel]
        src, dst, mode = src_root / rel, root / rel, manifest_mod.mode_bits(meta["mode"])
        if not os.path.lexists(str(dst)):
            _write_copy(src, dst, mode)
            report.added.append(rel)
            continue
        current = None
        if dst.is_file() and not dst.is_symlink():
            current = manifest_mod.hash_file(dst)
        if current == meta["sha256"]:
            if (dst.stat().st_mode & 0o777) != mode:
                os.chmod(str(dst), mode)
            report.unchanged.append(rel)
        elif current is not None and current in known.get(rel, ()):
            _write_copy(src, dst, mode)
            report.replaced.append(rel)
        else:
            _write_copy(src, _new_path(dst), mode)
            report.kept_modified.append(rel)
            log.info("kept edited %s; new version at %s%s", rel, rel, NEW_SUFFIX)


def _remove_dropped(root: Path, known: Dict[str, Set[str]], new: Dict[str, Any],
                    report: ApplyReport, log) -> None:
    for rel in sorted(set(known) - set(new)):
        dst = root / rel
        if not os.path.lexists(str(dst)):
            continue
        if (dst.is_file() and not dst.is_symlink()
                and manifest_mod.hash_file(dst) in known[rel]):
            dst.unlink()
            prune_empty_dirs(dst.parent, root)
            report.removed.append(rel)
        else:
            report.kept_removed_modified.append(rel)
            log.info("kept %s (no longer in the kit, but edited)", rel)


def prune_empty_dirs(start: Path, stop: Path, keep=frozenset()) -> None:
    """Remove now-empty folders from `start` up to (not including) `stop`."""
    current, stop_abs = Path(start), Path(stop)
    while current != stop_abs and stop_abs in current.parents:
        rel = current.relative_to(stop_abs).as_posix()
        if rel in keep:
            return
        try:
            os.rmdir(str(current))
        except OSError:
            return
        current = current.parent


def _create_templates(src_root: Path, root: Path, templates: List[str],
                      report: ApplyReport) -> None:
    for rel in templates:
        dst = root / rel
        if os.path.lexists(str(dst)):
            continue
        src = src_root / rel
        _write_copy(src, dst, manifest_mod.mode_bits(manifest_mod.file_mode(src)))
        report.templates_created.append(rel)


def rebuild_links(ws: Workspace) -> List[str]:
    """`.agents/skills/<name>` -> `../../.claude/skills/<name>` for every folder with SKILL.md.

    Removes links that dangle or point at skills that are gone; never deletes anything that is
    not a symlink. Returns the skill names that are linked.
    """
    skills, links_dir = ws.claude_skills, ws.agents_skills
    wanted = []
    if skills.is_dir():
        wanted = sorted(n for n in os.listdir(str(skills))
                        if (skills / n).is_dir() and (skills / n / "SKILL.md").is_file())
    if links_dir.is_symlink():  # the whole folder is a link: the user's setup, leave it alone
        same = os.path.realpath(str(links_dir)) == os.path.realpath(str(skills))
        return wanted if same else []
    links_dir.mkdir(parents=True, exist_ok=True)
    skills_real = os.path.realpath(str(skills))
    for entry in sorted(os.listdir(str(links_dir))):
        path = links_dir / entry
        if not path.is_symlink():
            continue
        target_real = os.path.realpath(str(path))
        into_skills = (target_real.startswith(skills_real + os.sep)
                       or os.readlink(str(path)).startswith(SKILL_LINK_PREFIX))
        if not path.exists() or (into_skills and entry not in wanted):
            path.unlink()
    linked = []
    for name in wanted:
        path, target = links_dir / name, SKILL_LINK_PREFIX + name
        if path.is_symlink():
            if os.readlink(str(path)) == target:
                linked.append(name)
                continue
            path.unlink()
        elif os.path.lexists(str(path)):
            continue  # a real file or folder of the user's: never touched
        os.symlink(target, str(path))
        linked.append(name)
    return linked


def sync_codex_rules(ws: Workspace, home: Path) -> str:
    """Copy the kit's Codex rules to ~/.codex/rules/ (Codex reads that folder for every project)."""
    src = ws.root / CODEX_RULES
    if not src.is_file():
        return ""
    dest = Path(home) / ".codex" / "rules" / Path(CODEX_RULES).name
    try:
        data = src.read_bytes()
        if dest.is_file() and dest.read_bytes() == data:
            return "unchanged"
        manifest_mod.atomic_write(dest, data, 0o644)
    except OSError as exc:
        return f"failed: {exc}"
    return "written"


def _state_path(ws: Workspace) -> Path:
    return ws.state / MIGRATIONS_STATE


def load_migration_state(ws: Workspace) -> Dict[str, Any]:
    try:
        data = json.loads(_state_path(ws).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict) or not isinstance(data.get("applied"), dict):
        data = {"applied": {}}
    return data


def _save_state(ws: Workspace, state: Dict[str, Any]) -> None:
    manifest_mod.atomic_write(_state_path(ws), manifest_mod.dumps(state).encode("utf-8"))


def _list_migrations(folder: Path):
    return [(mid, folder / f"{mid}.py") for mid in manifest_mod.migration_ids(folder)]


def _load_migration(mid: str, path: Path):
    spec = importlib.util.spec_from_file_location(f"brain_migration_{mid}", str(path))
    if spec is None or spec.loader is None:
        raise MigrationError(f"migration {mid} could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    func = getattr(module, "migrate", None)
    if not callable(func):
        raise MigrationError(f"migration {mid} has no migrate(workspace)")
    return func


def _run_migrations(folder: Path, ws: Workspace, mode: str, fresh: bool,
                    report: ApplyReport, log) -> None:
    available = _list_migrations(folder)
    state = load_migration_state(ws)
    applied = state["applied"]
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if mode == "install" and fresh:
        # A fresh install already has the newest layout: nothing to migrate.
        for mid, _ in available:
            applied.setdefault(mid, {"at": now, "ran": False, "message": "fresh install"})
        _save_state(ws, state)
        return
    for mid, path in available:
        if mid in applied:
            continue
        try:
            message = _load_migration(mid, path)(ws.root)
        except MigrationError:
            raise
        except Exception as exc:  # any failure aborts the apply; the caller rolls back
            raise MigrationError(f"migration {mid} failed: {exc}") from exc
        applied[mid] = {"at": now, "ran": True, "message": str(message or "")[:500]}
        _save_state(ws, state)  # after each one, so a later failure keeps the record straight
        report.migrations_run.append(mid)
        log.info("migration %s: %s", mid, message)


# --- CLI -----------------------------------------------------------------------------------

def register(subparsers) -> None:
    # No help= on purpose: internal command, kept out of `brain --help`.
    parser = subparsers.add_parser("_apply", description="(internal) apply a release folder")
    parser.add_argument("--release-dir", required=True)
    parser.add_argument("--workspace", default=argparse.SUPPRESS)
    parser.add_argument("--mode", choices=("install", "update"), default="update")
    parser.add_argument("--home", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--json", action="store_true")
    parser.set_defaults(func=run)


def _workspace(args) -> Workspace:
    value = getattr(args, "workspace", None)
    return Workspace(Path(value)) if value else Workspace.default()


def run(args) -> int:
    from .log import get_logger

    ws = _workspace(args)
    home = Path(args.home).expanduser() if getattr(args, "home", None) else None
    try:
        log = get_logger(ws)
        report = apply_release(Path(args.release_dir), ws, log, mode=args.mode, home=home)
    except MigrationError as exc:
        return _fail(args, "migration", str(exc), 3)
    except (ApplyError, OSError, ValueError) as exc:
        return _fail(args, "apply", str(exc), 1)
    if args.json:
        print(json.dumps({"ok": True, "report": report.to_dict()}, sort_keys=True))
    else:
        print(f"Applied brain kit v{report.version}.")
        for line in report.summary_lines():
            print(line)
    return 0


def _fail(args, kind: str, message: str, code: int) -> int:
    if getattr(args, "json", False):
        print(json.dumps({"ok": False, "kind": kind, "error": message}, sort_keys=True))
    print(f"brain _apply failed: {message}", file=sys.stderr)
    return code
