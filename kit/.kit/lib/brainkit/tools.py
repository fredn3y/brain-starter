"""`brain tools status|install`: the outside programs the brain relies on, pinned in `.kit/tools.json`.

Install types:
- script: the vendor's official installer, `curl -fsSL <url> | <shell> [-s <args>]`;
- archive-binary: pinned download, hash check, unpack, copy one binary to `dest` (0755);
- mac-app-zip: pinned download, hash check, `ditto -x -k`, then the .app goes to /Applications
  (or ~/Applications when /Applications isn't writable);
- manual: print the instructions.
Assets carry "sha256" and/or "sha512" (hex). A "TODO" url or hash is refused, never guessed.
"""

from __future__ import annotations

import hashlib
import json
import os
import plistlib
import re
import shlex
import shutil
import tarfile
import tempfile
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import http
from .runner import Runner
from .safe_tar import UnsafeArchiveError, safe_extract, safe_unzip

TODO = "TODO"
TYPES = ("script", "archive-binary", "mac-app-zip", "manual")
SHELLS = ("bash", "sh")
HASH_LENGTHS = {"sha256": 64, "sha512": 128}
ARCH_ALIASES = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "x86_64", "amd64": "x86_64"}
TAR_SUFFIXES = (".tar.gz", ".tgz", ".tar.xz", ".txz", ".tar.bz2", ".tar")
SYSTEM_APPS = Path("/Applications")
KIT_TOOLS_FILE = Path(__file__).resolve().parents[2] / "tools.json"
CHECK_TIMEOUT = 30
INSTALL_TIMEOUT = 1200


class ToolError(RuntimeError):
    pass


class NotConfigured(ToolError):
    pass


@dataclass
class ToolStatus:
    name: str
    required: bool
    installed: bool
    version: str = ""
    where: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Outcome:
    name: str
    status: str  # installed | present | manual | not-configured | failed
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.status in ("installed", "present", "manual")


def load_tools(path) -> List[Dict[str, Any]]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ToolError(f"can't read {path}: {exc}") from exc
    tools = data.get("tools") if isinstance(data, dict) else None
    if not isinstance(tools, list):
        raise ToolError(f"{path} has no tools list")
    for tool in tools:
        if not isinstance(tool, dict) or not isinstance(tool.get("name"), str):
            raise ToolError(f"{path}: every tool needs a name")
        if (tool.get("install") or {}).get("type") not in TYPES:
            raise ToolError(f"{path}: {tool['name']} has an unknown install type")
    return tools


def tools_file(ws) -> Path:
    """The workspace's tools.json, else the one shipped next to this code."""
    return ws.tools_file if ws.tools_file.is_file() else KIT_TOOLS_FILE


def select(tools: List[Dict[str, Any]], names: Optional[Sequence[str]]) -> List[Dict[str, Any]]:
    if not names:
        return list(tools)
    known = {t["name"]: t for t in tools}
    unknown = [n for n in names if n not in known]
    if unknown:
        raise ToolError(f"unknown tool(s): {', '.join(unknown)} (known: {', '.join(known)})")
    return [known[n] for n in names]


def detect_arch(runner: Runner) -> str:
    raw = runner.run(["uname", "-m"], timeout=10).stdout.strip()
    arch = ARCH_ALIASES.get(raw, raw or "unknown")
    if arch == "x86_64":  # an Apple Silicon Mac running a translated shell still says x86_64
        probe = runner.run(["sysctl", "-n", "hw.optional.arm64"], timeout=10)
        if probe.ok and probe.stdout.strip() == "1":
            arch = "arm64"
    return arch


# --- status -------------------------------------------------------------------------------

def _apps_dirs(home: Path, apps_dirs: Optional[Sequence[Path]]) -> List[Path]:
    if apps_dirs is not None:
        return [Path(p) for p in apps_dirs]
    return [SYSTEM_APPS, Path(home) / "Applications"]


def find_app(app: str, home: Path, apps_dirs: Optional[Sequence[Path]] = None) -> Optional[Path]:
    for folder in _apps_dirs(home, apps_dirs):
        if app and (folder / app).is_dir():
            return folder / app
    return None


def _app_version(app: Path) -> str:
    try:
        with open(str(app / "Contents" / "Info.plist"), "rb") as handle:
            info = plistlib.load(handle)
        return str(info.get("CFBundleShortVersionString") or info.get("CFBundleVersion") or "")
    except Exception:  # a broken plist must not break `brain tools status`
        return ""


def expand(path_text: str, home: Path) -> Path:
    if path_text.startswith("~/"):
        return Path(home) / path_text[2:]
    path = Path(path_text)
    if not path.is_absolute():
        raise ToolError(f"dest must start with ~/ or /: {path_text}")
    return path


def _first_line(*texts: str) -> str:
    for text in texts:
        for line in (text or "").splitlines():
            if line.strip():
                return line.strip()[:200]
    return ""


def tool_status(tool: Dict[str, Any], runner: Runner, home: Path,
                apps_dirs: Optional[Sequence[Path]] = None) -> ToolStatus:
    name, required = tool["name"], bool(tool.get("required"))
    install = tool.get("install") or {}
    if install.get("type") == "mac-app-zip":
        app = find_app(str(install.get("app") or ""), home, apps_dirs)
        return ToolStatus(name, required, app is not None, _app_version(app) if app else "",
                          str(app or ""))
    check = [str(arg) for arg in tool.get("check") or []]
    flags = check[1:] or ["--version"]
    candidates = []
    if install.get("type") == "archive-binary" and install.get("dest"):
        dest = expand(str(install["dest"]), home)
        if dest.is_file():
            candidates.append([str(dest)] + flags)
    if check:
        candidates.append(check)
        local = Path(home) / ".local" / "bin" / check[0]
        if "/" not in check[0] and local.is_file():  # installers drop binaries here, off PATH
            candidates.append([str(local)] + flags)
    for argv in candidates:
        result = runner.run(argv, timeout=CHECK_TIMEOUT)
        if result.ok:
            version = _first_line(result.stdout, result.stderr)
            return ToolStatus(name, required, True, version, argv[0])
    return ToolStatus(name, required, False)


def statuses(ws, runner: Runner, home: Optional[Path] = None) -> List[ToolStatus]:
    """For `brain doctor` and `brain tools status`."""
    home = Path(home) if home is not None else Path.home()
    return [tool_status(t, runner, home) for t in load_tools(tools_file(ws))]


# --- install ------------------------------------------------------------------------------

def _asset(tool: Dict[str, Any], arch: str) -> Tuple[str, Dict[str, str]]:
    name = tool["name"]
    asset = (tool["install"].get("assets") or {}).get(arch)
    if not isinstance(asset, dict):
        raise ToolError(f"no {name} download for this Mac ({arch})")
    url = str(asset.get("url") or "")
    hashes = {algo: str(asset[algo]).strip().lower() for algo in HASH_LENGTHS if asset.get(algo)}
    if not url or url == TODO or not hashes or TODO.lower() in hashes.values():
        raise NotConfigured(f"{name} can't be installed automatically yet (its download isn't "
                            "pinned in tools.json)")
    if not url.startswith("https://"):
        raise ToolError(f"{name}: refusing a non-https download")
    for algo, value in hashes.items():
        if not re.match(r"^[0-9a-f]{%d}$" % HASH_LENGTHS[algo], value):
            raise ToolError(f"{name}: bad {algo} in tools.json")
    return url, hashes


def _download(runner: Runner, url: str, work: Path) -> Path:
    name = re.sub(r"[^A-Za-z0-9._-]", "_", url.rsplit("/", 1)[-1].split("?", 1)[0]) or "download"
    path = work / name
    http.download(runner, url, str(path))
    if not path.is_file():
        raise ToolError(f"download of {url} produced no file")
    return path


def _verify(path: Path, hashes: Dict[str, str]) -> None:
    for algo, expected in hashes.items():
        digest = hashlib.new(algo)
        with open(str(path), "rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 16), b""):
                digest.update(chunk)
        if digest.hexdigest() != expected:
            raise ToolError(f"the download didn't match its {algo} checksum; nothing was installed")


def _find(root: Path, name: str, want_dir: bool) -> Optional[Path]:
    """Shallowest entry called `name` (a plain file, or a folder when want_dir)."""
    best = None
    for dirpath, dirnames, filenames in os.walk(str(root)):
        dirnames.sort()
        for entry in (dirnames if want_dir else filenames):
            path = Path(dirpath) / entry
            if entry == name and not path.is_symlink():
                depth = len(path.relative_to(root).parts)
                if best is None or depth < best[0]:
                    best = (depth, path)
    return best[1] if best else None


def _install_script(tool: Dict[str, Any], runner: Runner, env: Dict[str, str]) -> None:
    install = tool["install"]
    url, shell = str(install.get("url") or ""), str(install.get("shell") or "bash")
    if not url or url == TODO:
        raise NotConfigured(f"{tool['name']} has no installer address yet")
    if not url.startswith("https://"):
        raise ToolError(f"{tool['name']}: refusing a non-https installer")
    if shell not in SHELLS:
        raise ToolError(f"{tool['name']}: unsupported shell {shell!r}")
    command = f"curl -fsSL {shlex.quote(url)} | {shell}"
    args = [str(a) for a in install.get("args") or []]
    if args:
        command += " -s " + " ".join(shlex.quote(a) for a in args)
    # pipefail: without it a failed download feeds the shell an empty script and "succeeds"
    result = runner.run(["bash", "-c", "set -o pipefail; " + command], env=env,
                        timeout=INSTALL_TIMEOUT)
    if not result.ok:
        raise ToolError(f"the installer stopped with code {result.returncode}: "
                        f"{_first_line(result.stderr, result.stdout)}")


def _install_archive_binary(tool: Dict[str, Any], runner: Runner, home: Path, arch: str) -> None:
    install = tool["install"]
    url, hashes = _asset(tool, arch)
    binary = str(install.get("binary") or tool["name"])
    work = Path(tempfile.mkdtemp(prefix="brain-tool-"))
    try:
        archive = _download(runner, url, work)
        _verify(archive, hashes)
        unpack = work / "unpacked"
        lower = archive.name.lower()
        if lower.endswith(TAR_SUFFIXES):
            safe_extract(archive, unpack)
        elif lower.endswith(".zip"):
            safe_unzip(archive, unpack)
        else:  # the download is the binary itself
            unpack.mkdir()
            shutil.copyfile(str(archive), str(unpack / binary))
        found = _find(unpack, binary, want_dir=False)
        if found is None:
            raise ToolError(f"{binary} wasn't found inside the download")
        dest = expand(str(install.get("dest") or f"~/.local/bin/{binary}"), home)
        dest.parent.mkdir(parents=True, exist_ok=True)
        staging = dest.with_name(f".{dest.name}.brain-tmp")
        shutil.copyfile(str(found), str(staging))
        os.chmod(str(staging), 0o755)
        os.replace(str(staging), str(dest))
    finally:
        shutil.rmtree(str(work), ignore_errors=True)


def _apps_target(home: Path, apps_dirs: Optional[Sequence[Path]]) -> Path:
    folders = _apps_dirs(home, apps_dirs)
    system, user = folders[0], folders[-1]
    if system.is_dir() and os.access(str(system), os.W_OK):
        return system
    user.mkdir(parents=True, exist_ok=True)
    return user


def _remove_tree(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(str(path))


def _install_mac_app(tool: Dict[str, Any], runner: Runner, home: Path, arch: str,
                     apps_dirs: Optional[Sequence[Path]]) -> None:
    app = str(tool["install"].get("app") or "")
    if not app.endswith(".app") or "/" in app:
        raise ToolError(f"{tool['name']}: bad app name {app!r}")
    url, hashes = _asset(tool, arch)
    work = Path(tempfile.mkdtemp(prefix="brain-tool-"))
    try:
        archive = _download(runner, url, work)
        _verify(archive, hashes)
        unpack = work / "unpacked"
        unpack.mkdir()
        result = runner.run(["ditto", "-x", "-k", str(archive), str(unpack)], timeout=INSTALL_TIMEOUT)
        if not result.ok:
            raise ToolError(f"couldn't unpack {app}: "
                            f"{_first_line(result.stderr, result.stdout)}")
        source = _find(unpack, app, want_dir=True)
        if source is None:
            raise ToolError(f"{app} wasn't found inside the download")
        target = _apps_target(home, apps_dirs)
        dest, staging, old = target / app, target / f"{app}.brain-new", target / f"{app}.brain-old"
        for leftover in (staging, old):
            _remove_tree(leftover)
        result = runner.run(["ditto", str(source), str(staging)], timeout=INSTALL_TIMEOUT)
        if not result.ok:
            raise ToolError(f"couldn't copy {app} into {target}: "
                            f"{_first_line(result.stderr, result.stdout)}")
        if os.path.lexists(str(dest)):
            os.rename(str(dest), str(old))
        os.rename(str(staging), str(dest))
        _remove_tree(old)
    finally:
        shutil.rmtree(str(work), ignore_errors=True)


def install_tool(tool: Dict[str, Any], runner: Runner, home: Path, log, arch: Optional[str] = None,
                 force: bool = False, apps_dirs: Optional[Sequence[Path]] = None) -> Outcome:
    name, install = tool["name"], tool.get("install") or {}
    kind = install.get("type")
    home = Path(home)
    if not force:
        status = tool_status(tool, runner, home, apps_dirs)
        if status.installed:
            return Outcome(name, "present", status.version or "already installed")
    env = dict(os.environ)
    env["HOME"] = str(home)
    try:
        if kind == "manual":
            text = str(install.get("instructions") or "").strip()
            if not text or text == TODO:
                raise NotConfigured(f"{name} has no install steps yet")
            return Outcome(name, "manual", text)
        if kind == "script":
            _install_script(tool, runner, env)
        elif kind == "archive-binary":
            _install_archive_binary(tool, runner, home, arch or detect_arch(runner))
        elif kind == "mac-app-zip":
            _install_mac_app(tool, runner, home, arch or detect_arch(runner), apps_dirs)
        else:
            raise ToolError(f"unknown install type {kind!r}")
    except NotConfigured as exc:
        return Outcome(name, "not-configured", f"{exc}. Ask your consultant.")
    except (ToolError, http.HttpError, UnsafeArchiveError, tarfile.TarError, zipfile.BadZipFile,
            OSError) as exc:
        log.warning("tool %s: install failed: %s", name, exc)
        return Outcome(name, "failed", str(exc))
    status = tool_status(tool, runner, home, apps_dirs)
    if not status.installed:
        return Outcome(name, "failed", "the installer finished, but the tool doesn't run yet")
    log.info("tool %s installed (%s)", name, status.version)
    return Outcome(name, "installed", status.version)


def install_many(tools: List[Dict[str, Any]], runner: Runner, home: Path, log,
                 names: Optional[Sequence[str]] = None, force: bool = False,
                 apps_dirs: Optional[Sequence[Path]] = None) -> List[Outcome]:
    chosen = select(tools, names)
    arch = None
    outcomes = []
    for tool in chosen:
        if arch is None and (tool.get("install") or {}).get("type") in ("archive-binary",
                                                                         "mac-app-zip"):
            arch = detect_arch(runner)
        outcomes.append(install_tool(tool, runner, home, log, arch=arch, force=force,
                                     apps_dirs=apps_dirs))
    return outcomes


def outcome_line(outcome: Outcome) -> str:
    labels = {"installed": "installed", "present": "already installed", "manual": "to do by hand",
              "not-configured": "not ready yet", "failed": "FAILED"}
    text = f"{outcome.name}: {labels.get(outcome.status, outcome.status)}"
    return f"{text} ({outcome.message})" if outcome.message else text


# --- CLI ----------------------------------------------------------------------------------

def register(subparsers) -> None:
    parser = subparsers.add_parser("tools", help="check or install the outside programs the brain uses")
    actions = parser.add_subparsers(dest="tools_command", metavar="<action>")
    status = actions.add_parser("status", help="show which tools are installed")
    status.add_argument("--json", action="store_true")
    status.set_defaults(func=_cmd_status)
    install = actions.add_parser("install", help="install missing tools")
    install.add_argument("names", nargs="*", help="only these tools (default: all)")
    install.add_argument("--force", action="store_true", help="reinstall even if present")
    install.set_defaults(func=_cmd_install)
    parser.set_defaults(func=_cmd_status, json=False)


def _workspace(args):
    from .paths import Workspace

    value = getattr(args, "workspace", None)
    return Workspace(Path(value)) if value else Workspace.default()


def _cmd_status(args) -> int:
    try:
        rows = statuses(_workspace(args), Runner())
    except ToolError as exc:
        print(f"brain tools: {exc}")
        return 2
    if args.json:
        print(json.dumps([r.to_dict() for r in rows], indent=2))
    else:
        for row in rows:
            state = "installed" if row.installed else "missing"
            need = "required" if row.required else "optional"
            print(f"{row.name:<8} {need:<9} {state:<10} {row.version}".rstrip())
    return 0 if all(r.installed for r in rows if r.required) else 1


def _cmd_install(args) -> int:
    from .log import get_logger

    ws = _workspace(args)
    try:
        tools = load_tools(tools_file(ws))
        outcomes = install_many(tools, Runner(), Path.home(), get_logger(ws), names=args.names,
                                force=args.force)
    except ToolError as exc:
        print(f"brain tools: {exc}")
        return 2
    for outcome in outcomes:
        print(outcome_line(outcome))
    return 0 if all(o.ok for o in outcomes) else 1
