"""`brain update` and `brain rollback`: move between GitHub releases without touching the user's files.

The installed (old) code drives; the new release applies itself:
check GitHub -> download tarball + SHA256SUMS -> verify -> extract -> doctor (before) ->
snapshot -> new release's `brain _apply` -> doctor (after) -> roll back if the apply failed or
a health check newly fails.

Everything this module needs is imported up front: `_apply` replaces the kit's own files on disk
while this process is still running, and a late import would load the new version.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shutil
import sys
import tarfile
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Iterator, List, Optional, Tuple

from . import config as config_mod
from . import http
from . import manifest as manifest_mod
from . import snapshot as snapshot_mod
from .apply import ApplyReport, load_incoming
from .paths import Workspace
from .runner import Runner
from .safe_tar import UnsafeArchiveError, safe_extract

try:
    from .lock import try_lock as _shared_lock
except ImportError:  # pragma: no cover - lock.py ships with the kit
    _shared_lock = None

API_LATEST = "https://api.github.com/repos/{repo}/releases/latest"
API_HEADERS = {"Accept": "application/vnd.github+json", "User-Agent": "brain-starter"}
TARBALL_RE = re.compile(r"^brain-starter-v\d+(\.\d+)*\.tar\.gz$")
_VERSION_RE = re.compile(r"^[vV]?(\d+)(?:\.(\d+))?(?:\.(\d+))?")
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
APPLY_TIMEOUT = 900
DOCTOR_TIMEOUT = 300
BUSY = "Another brain task is running right now (a catch-up or an update). Try again in a few minutes."


class ReleaseError(RuntimeError):
    pass


@dataclass
class Release:
    version: str
    tag: str
    tarball_url: str
    sums_url: str
    notes: str = ""
    html_url: str = ""


def parse_version(text) -> Tuple[int, int, int]:
    """'v0.3.1' -> (0, 3, 1); missing parts count as 0. Raises ValueError for junk."""
    match = _VERSION_RE.match(str(text or "").strip())
    if not match:
        raise ValueError(f"not a version: {text!r}")
    major, minor, patch = (int(part or 0) for part in match.groups())
    return major, minor, patch


def installed_version(ws: Workspace) -> str:
    """Version of the last completed install: the manifest's (written last by `_apply`), else
    VERSION. After an interrupted apply this is the older version, so the update is offered again."""
    try:
        version = ws.version_file.read_text(encoding="utf-8").strip() or "0.0.0"
    except OSError:
        version = "0.0.0"
    try:
        completed = manifest_mod.load(ws.manifest).get("version")
    except (OSError, ValueError):
        return version
    return completed if isinstance(completed, str) and completed else version


def check_latest(runner: Runner, repo: str, timeout: int = 30) -> Optional[Release]:
    """The latest published release, or None when the repo has none yet."""
    if not _REPO_RE.match(repo or ""):
        raise ReleaseError(f"bad kit_repo: {repo!r}")
    resp = http.request(runner, "GET", API_LATEST.format(repo=repo), headers=API_HEADERS,
                        timeout=timeout)
    if resp.status == 404:
        return None
    if resp.status != 200:
        raise http.HttpError(f"GitHub answered HTTP {resp.status}")
    try:
        data = resp.json()
    except ValueError as exc:
        raise ReleaseError("GitHub sent an unreadable answer") from exc
    return release_from_api(data)


def release_from_api(data) -> Release:
    if not isinstance(data, dict):
        raise ReleaseError("unexpected answer from GitHub")
    tag = str(data.get("tag_name") or "")
    version = tag[1:] if tag[:1] in ("v", "V") else tag
    parse_version(version)
    assets = [a for a in data.get("assets") or [] if isinstance(a, dict)]
    urls = {str(a.get("name", "")): str(a.get("browser_download_url") or "") for a in assets}
    tarball = urls.get(f"brain-starter-v{version}.tar.gz") or next(
        (url for name, url in sorted(urls.items()) if TARBALL_RE.match(name)), "")
    sums = urls.get("SHA256SUMS", "")
    if not tarball or not sums:
        raise ReleaseError(f"release {tag} has no kit download yet")
    for url in (tarball, sums):
        if not url.startswith("https://"):
            raise ReleaseError(f"refusing a non-https download: {url}")
    return Release(version=version, tag=tag, tarball_url=tarball, sums_url=sums,
                   notes=str(data.get("body") or ""), html_url=str(data.get("html_url") or ""))


def parse_sums(text: str) -> Dict[str, str]:
    """SHA256SUMS lines ('<hex>  <name>' or '<hex> *<name>') -> {name: hex}."""
    out = {}
    for line in text.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2 and re.match(r"^[0-9a-fA-F]{64}$", parts[0]):
            out[parts[1].lstrip("*").strip()] = parts[0].lower()
    return out


# --- locking, subprocess helpers ----------------------------------------------------------

@contextmanager
def _flock(path: Path) -> Iterator[bool]:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def workspace_lock(ws: Workspace):
    """Context manager yielding True while this process holds the run lock (catch-up, update,
    rollback and re-install all take it), or False at once when someone else does."""
    if _shared_lock is not None:
        return _shared_lock(ws.lock_file)
    return _flock(ws.lock_file)



def _child_env(ws: Workspace, home: Optional[Path]) -> Dict[str, str]:
    env = dict(os.environ)
    env["BRAIN_HOME"] = str(ws.root)
    if home is not None:
        env["HOME"] = str(home)
    return env


def _last_json(text: str):
    """The JSON a command printed, tolerating stray lines before it."""
    text = (text or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        pass
    for match in re.finditer(r"^[\[{]", text, re.M):  # a pretty-printed document after noise
        try:
            return json.loads(text[match.start():])
        except ValueError:
            continue
    for line in reversed(text.splitlines()):
        try:
            return json.loads(line)
        except ValueError:
            continue
    return None


def doctor_checks(ws: Workspace, runner: Runner, python: str,
                  env: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """{check name: status} from `brain doctor --quick --json`; {} when it can't run."""
    brain = ws.kit_bin / "brain"
    if not brain.is_file():
        return {}
    result = runner.run([python, str(brain), "--workspace", str(ws.root), "doctor", "--quick",
                         "--json"], env=env, timeout=DOCTOR_TIMEOUT, cwd=str(ws.root))
    data = _last_json(result.stdout)
    if isinstance(data, dict):
        data = data.get("checks")
    checks = {}
    for item in data if isinstance(data, list) else []:
        if isinstance(item, dict) and item.get("name"):
            checks[str(item["name"])] = str(item.get("status", ""))
    return checks


def doctor_regressions(before: Dict[str, str], after: Dict[str, str]) -> List[str]:
    """Checks that existed before the update with a non-fail status and fail after it.

    New, renamed or already-failing checks never count: they would roll back every attempt.
    """
    return sorted(name for name, status in after.items()
                  if status == "fail" and name in before and before[name] != "fail")


def _doctor_problem(before: Dict[str, str], after: Dict[str, str]) -> Optional[str]:
    if not before:  # no baseline (doctor missing or crashed): don't judge by health checks
        return None
    if not after:
        return "the health check stopped working after the update"
    worse = doctor_regressions(before, after)
    return "these health checks started failing: " + ", ".join(worse) if worse else None


def _say(out) -> Callable[[str], None]:
    stream = out if out is not None else sys.stdout
    return lambda message: print(message, file=stream)


def _notes_excerpt(notes: str, max_lines: int = 12, max_chars: int = 800) -> List[str]:
    lines = [line.rstrip() for line in (notes or "").strip()[:max_chars].splitlines()]
    return [line for line in lines if line.strip()][:max_lines]


def _tail(text: str, limit: int = 300) -> str:
    lines = [line for line in (text or "").strip().splitlines() if line.strip()]
    return (lines[-1] if lines else "")[:limit]


# --- update -------------------------------------------------------------------------------

def run_update(ws: Workspace, config, runner: Runner, log, yes: bool = False,
               release: Optional[Release] = None, home: Optional[Path] = None, *,
               check_only: bool = False, ask: Optional[Callable[[str], str]] = None,
               interactive: Optional[Callable[[], bool]] = None, out=None,
               python: Optional[str] = None) -> int:
    """0 = updated or nothing to do, 1 = failed (nothing changed, or rolled back), 2 = needs help."""
    say = _say(out)
    with workspace_lock(ws) as held:
        if not held:
            say(BUSY)
            return 1
        current = installed_version(ws)
        if release is None:
            try:
                release = check_latest(runner, config.kit_repo)
            except (http.HttpError, ReleaseError, ValueError) as exc:
                say(f"Couldn't check for updates: {exc}")
                return 1
            if release is None:
                say("No releases have been published yet.")
                return 0
        try:
            newer = parse_version(release.version) > parse_version(current)
        except ValueError as exc:
            say(f"Couldn't compare versions: {exc}")
            return 1
        if not newer:
            say(f"Already up to date (v{current}).")
            return 0
        say(f"Update available: v{current} -> v{release.version}")
        for line in _notes_excerpt(release.notes):
            say(f"  {line}")
        if release.html_url:
            say(f"  Details: {release.html_url}")
        if check_only:
            return 0
        if not yes:
            if not (interactive() if interactive else sys.stdin.isatty()):
                say("Nothing has changed yet. Run `brain update --yes` to apply it.")
                return 0
            if (ask or input)("Update now? [y/N] ").strip().lower() not in ("y", "yes"):
                say("No changes made.")
                return 0
        cache = ws.kit / "cache"
        cache.mkdir(parents=True, exist_ok=True)
        work = Path(tempfile.mkdtemp(prefix="update-", dir=str(cache)))
        try:
            return _download_and_apply(ws, runner, log, release, current, work, home,
                                       python or sys.executable or "python3", say)
        finally:
            shutil.rmtree(str(work), ignore_errors=True)


def _fetch_verified(runner: Runner, release: Release, work: Path, say) -> Optional[Path]:
    name = release.tarball_url.rsplit("/", 1)[-1].split("?", 1)[0]
    if not TARBALL_RE.match(name):
        name = f"brain-starter-v{release.version}.tar.gz"
    tarball, sums = work / name, work / "SHA256SUMS"
    try:
        http.download(runner, release.tarball_url, str(tarball))
        http.download(runner, release.sums_url, str(sums))
        expected = parse_sums(sums.read_text(encoding="utf-8", errors="replace")).get(name)
        actual = manifest_mod.hash_file(tarball)
    except (http.HttpError, OSError) as exc:
        say(f"Couldn't download the update ({exc}). Nothing was changed.")
        return None
    if not expected:
        say(f"The checksum list has no entry for {name}, so it wasn't used. Nothing was changed.")
        return None
    if actual != expected:
        say("The download didn't match its checksum, so it wasn't used. Nothing was changed. "
            "Try again later.")
        return None
    return tarball


def _release_dir(root: Path, version: str) -> Path:
    expected = root / f"brain-starter-v{version}"
    candidates = [expected] if expected.is_dir() else [p for p in root.iterdir() if p.is_dir()]
    if len(candidates) != 1 or not (candidates[0] / "manifest.json").is_file():
        raise ReleaseError("the download doesn't look like a brain release")
    if not (candidates[0] / "kit" / ".kit" / "bin" / "brain").is_file():
        raise ReleaseError("the release has no brain command")
    return candidates[0]


def _download_and_apply(ws: Workspace, runner: Runner, log, release: Release, current: str,
                        work: Path, home: Optional[Path], python: str, say) -> int:
    tarball = _fetch_verified(runner, release, work, say)
    if tarball is None:
        return 1
    try:
        safe_extract(tarball, work / "release")
        release_dir = _release_dir(work / "release", release.version)
        new_manifest = manifest_mod.validate(manifest_mod.load(release_dir / "manifest.json"))
        if new_manifest["version"] != release.version:
            raise ReleaseError(f"it says v{new_manifest['version']}, expected v{release.version}")
    except (UnsafeArchiveError, tarfile.TarError, OSError, ValueError, ReleaseError) as exc:
        say(f"The update download looks broken ({exc}). Nothing was changed.")
        return 1
    env = _child_env(ws, home)
    before = doctor_checks(ws, runner, python, env)
    try:
        snap = snapshot_mod.create(ws, f"v{current}-to-v{release.version}")
    except OSError as exc:
        say(f"Couldn't save a safety copy before updating ({exc}). Nothing was changed.")
        return 1
    log.info("update v%s -> v%s: snapshot %s", current, release.version, snap.name)
    payload, problem = None, None
    try:
        result = runner.run([python, str(release_dir / "kit" / ".kit" / "bin" / "brain"), "_apply",
                             "--release-dir", str(release_dir), "--workspace", str(ws.root),
                             "--mode", "update", "--json"],
                            env=env, timeout=APPLY_TIMEOUT, cwd=str(ws.root))
        payload = _last_json(result.stdout)
        if not result.ok or not (isinstance(payload, dict) and payload.get("ok")):
            detail = payload.get("error") if isinstance(payload, dict) else None
            problem = "the update step failed: " + str(
                detail or _tail(result.stderr) or f"exit code {result.returncode}")
        else:
            problem = _doctor_problem(before, doctor_checks(ws, runner, python, env))
    except KeyboardInterrupt:
        problem = "it was stopped before it finished"
    if problem:
        return _roll_back(ws, snap, new_manifest, release_dir, home, problem, release.version,
                          log, say)
    report = ApplyReport.from_dict(payload.get("report") or {})
    say(f"Updated your brain from v{current} to v{release.version}.")
    for line in report.summary_lines():
        say(f"  {line}")
    say(f"A safety copy was saved first ({snap.name}); `brain rollback` goes back to it.")
    return 0


def _roll_back(ws, snap, new_manifest, release_dir, home, problem, version, log, say) -> int:
    log.error("update to v%s failed (%s); restoring %s", version, problem, snap.name)
    try:
        snapshot_mod.restore_after_failed_update(ws, snap, new_manifest, release_dir=release_dir,
                                                 home=home, log=log)
    except Exception as exc:  # report, never raise: the user needs the message below
        log.exception("restore failed")
        say(f"The update to v{version} failed ({problem}), and putting things back failed "
            f"too ({exc}).")
        say(f"Your files are saved in .kit/snapshots/{snap.name}. Please run `brain report` "
            "and send the file it makes to your consultant.")
        return 2
    say(f"The update to v{version} didn't go through ({problem}).")
    say("Your brain was put back exactly as it was before. Nothing was lost.")
    return 1


# --- rollback -----------------------------------------------------------------------------

def rollback(ws: Workspace, runner: Runner, log, snapshot: Optional[str] = None, *,
             home: Optional[Path] = None, out=None, python: Optional[str] = None) -> int:
    """Go back to the newest (or the named) safety copy. The user's newer files are kept."""
    say = _say(out)
    with workspace_lock(ws) as held:
        if not held:
            say(BUSY)
            return 1
        snaps = snapshot_mod.list_snapshots(ws)
        if not snaps:
            say("There are no safety copies to go back to yet.")
            return 1
        chosen = snaps[0]
        if snapshot:
            matches = [s for s in snaps if s.name in (snapshot, snapshot + snapshot_mod.SUFFIX)]
            if not matches:
                say(f"No safety copy called {snapshot}. `brain rollback --list` shows them.")
                return 1
            chosen = matches[0]
        unfinished = load_incoming(ws)  # an update cut off half-way: that's what's on disk
        before = unfinished["version"] if unfinished else installed_version(ws)
        try:
            report = snapshot_mod.restore_by_hand(ws, chosen, home=home, log=log)
        except (UnsafeArchiveError, tarfile.TarError, OSError, ValueError) as exc:
            log.exception("rollback failed")
            say(f"Couldn't go back to {chosen.name} ({exc}). Please run `brain report` and send "
                "the file it makes to your consultant.")
            return 2
        say(f"Went back from v{before} to v{installed_version(ws)} ({chosen.name}).")
        if report.kept_modified:
            say("You changed these kit files after that copy, so they were kept. "
                "The older version is beside each one:")
            for rel in report.kept_modified:
                say(f"  {rel}.new")
        if report.kept_edited:
            say("Kept because you changed them: " + ", ".join(report.kept_edited))
        checks = doctor_checks(ws, runner, python or sys.executable or "python3",
                               _child_env(ws, home))
        fails = sorted(name for name, status in checks.items() if status == "fail")
        if fails:
            say("Health check problems: " + ", ".join(fails) + ". Run `brain doctor` for details.")
        return 0


def list_snapshots_text(ws: Workspace) -> List[str]:
    rows = []
    for path in snapshot_mod.list_snapshots(ws):
        rows.append(f"{path.name}  ({path.stat().st_size / 1_000_000:.1f} MB)")
    return rows


# --- CLI ----------------------------------------------------------------------------------

def register(subparsers) -> None:
    up = subparsers.add_parser("update", help="get the newest brain kit (your files are never touched)")
    up.add_argument("--check", action="store_true", help="only check for a new version")
    up.add_argument("--yes", action="store_true", help="update without asking")
    up.set_defaults(func=_cmd_update)
    back = subparsers.add_parser("rollback", help="go back to before the last update")
    back.add_argument("--snapshot", metavar="NAME", help="which safety copy (see --list)")
    back.add_argument("--list", action="store_true", help="list the safety copies")
    back.set_defaults(func=_cmd_rollback)


def _workspace(args) -> Workspace:
    value = getattr(args, "workspace", None)
    return Workspace(Path(value)) if value else Workspace.default()


def _cmd_update(args: argparse.Namespace) -> int:
    from .log import get_logger

    ws = _workspace(args)
    return run_update(ws, config_mod.load(ws), Runner(), get_logger(ws), yes=args.yes,
                      check_only=args.check)


def _cmd_rollback(args: argparse.Namespace) -> int:
    from .log import get_logger

    ws = _workspace(args)
    if args.list:
        rows = list_snapshots_text(ws)
        print("\n".join(rows) if rows else "No safety copies yet.")
        return 0
    return rollback(ws, Runner(), get_logger(ws), snapshot=args.snapshot)
