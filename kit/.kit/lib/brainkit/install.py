"""`brain install`: first-time setup from a downloaded release (install.sh runs this).

Safe to re-run: it is an apply in install mode, so the user's files are never touched and edited
kit files get a `<file>.new` beside them. Over an existing brain it behaves like an update: it
takes the run lock, snapshots first and restores the snapshot if the apply fails. It also links
~/.local/bin/brain, adds one PATH block to ~/.zprofile, and installs the standard tools (the
`default` ones in tools.json: Claude Code and T3 Code), plus any add-ons named with --with. The
brain folder is always left in place; if a required tool (Claude Code) didn't install, it says so
and exits EX_TOOLS (install.sh shows a calm "paste the line again" message).
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from . import manifest as manifest_mod
from . import snapshot as snapshot_mod
from .apply import ApplyError, ApplyReport, apply_release
from .paths import Workspace
from .runner import Runner
from .tools import ToolError, default_names, install_many, load_tools, outcome_line
from .update import BUSY, installed_version, workspace_lock

EX_TEMPFAIL = 75  # "busy, try again later": install.sh shows a calm retry message
EX_TOOLS = 3  # the brain folder is in place, but a required tool didn't install
BLOCK_BEGIN = "# >>> brain >>>"
BLOCK_END = "# <<< brain <<<"
_BLOCK_RE = re.compile(re.escape(BLOCK_BEGIN) + r".*?" + re.escape(BLOCK_END) + r"[^\n]*\n?",
                       re.S)


def display(path: Path, home: Path) -> str:
    """'~/brain' for paths under home, else the full path."""
    full, base = os.path.abspath(str(path)), os.path.abspath(str(home))
    if full == base:
        return "~"
    if full.startswith(base.rstrip(os.sep) + os.sep):
        return "~/" + full[len(base.rstrip(os.sep)) + 1:]
    return full


def _same_path(a: Path, b: Path) -> bool:
    return (os.path.abspath(str(a)) == os.path.abspath(str(b))
            or os.path.realpath(str(a)) == os.path.realpath(str(b)))


def _dq(text: str) -> str:
    """Escape text for use inside double quotes in zsh or bash."""
    return re.sub(r'(["\\$`])', r"\\\1", text)


def path_block(ws: Workspace, home: Path) -> str:
    # The kit's bin comes first so `gws` finds the kit's guard shim before the real binary.
    if _same_path(ws.root, Path(home) / "brain"):
        kit_bin = "$HOME/brain/.kit/bin"
    else:
        kit_bin = _dq(os.path.abspath(str(ws.kit_bin)))
    return f'{BLOCK_BEGIN}\nexport PATH="{kit_bin}:$HOME/.local/bin:$PATH"\n{BLOCK_END}\n'


def ensure_path_block(ws: Workspace, home: Path) -> Path:
    """Add (or refresh) the one brain block in ~/.zprofile; nothing else in the file changes."""
    profile = Path(home) / ".zprofile"
    target = Path(os.path.realpath(str(profile))) if profile.is_symlink() else profile
    try:
        text = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        text = ""
    block = path_block(ws, home)
    matches = list(_BLOCK_RE.finditer(text))
    if matches:
        first = matches[0]
        new = text[:first.start()] + block + _BLOCK_RE.sub("", text[first.end():])
    else:
        new = text + ("" if not text else "\n" if text.endswith("\n") else "\n\n") + block
    if new != text:
        mode = target.stat().st_mode & 0o777 if target.exists() else 0o644
        manifest_mod.atomic_write(target, new.encode("utf-8"), mode)
    return target


def link_cli(ws: Workspace, home: Path) -> Optional[str]:
    """~/.local/bin/brain -> <ws>/.kit/bin/brain. Returns a warning when a real file is in the way."""
    link = Path(home) / ".local" / "bin" / "brain"
    target = os.path.abspath(str(ws.kit_bin / "brain"))
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.is_symlink():
        if os.readlink(str(link)) == target:
            return None
    elif os.path.lexists(str(link)):
        return f"{display(link, home)} already exists and isn't a link, so it was left alone"
    staging = link.with_name(".brain.brain-tmp")
    if os.path.lexists(str(staging)):
        staging.unlink()
    os.symlink(target, str(staging))
    os.replace(str(staging), str(link))
    return None


def next_steps(where: str, codex: bool = False) -> str:
    lines = [
        "",
        "Next steps:",
        "  1. Open a new Terminal window (the `brain` and `claude` commands only work in a new one).",
        f"  2. In it, run `cd {where} && claude`, say yes when it asks whether to trust the folder,",
        "     log in with your Claude account, then type /exit.",
    ]
    if codex:
        lines.append("  3. Optional: run `codex login` if you also use Codex.")
    lines += [
        f"  {4 if codex else 3}. Open T3 Code, add the folder {where}, start a Claude thread and type:",
        "     set me up",
    ]
    return "\n".join(lines)


def split_names(values: Optional[Sequence[str]]) -> List[str]:
    """['gws,codex', 'x'] -> ['gws', 'codex', 'x'] (commas or spaces, duplicates dropped)."""
    names: List[str] = []
    for value in values or []:
        for name in re.split(r"[\s,]+", value or ""):
            if name and name not in names:
                names.append(name)
    return names


def _release_manifest(release_dir) -> dict:
    try:
        return manifest_mod.validate(manifest_mod.load(Path(release_dir) / "manifest.json"))
    except (OSError, ValueError):
        return manifest_mod.empty()  # apply_release will refuse it before writing anything


def _apply_over_existing(release_dir, ws: Workspace, home: Path, log,
                         say) -> Tuple[Optional[ApplyReport], int]:
    """Re-running the installer over an existing brain is an update: same lock (so a catch-up
    never runs on half-swapped code), a snapshot first, and an exact restore if it fails.

    Returns (report, 0) on success, else (None, exit code): EX_TEMPFAIL when busy, 1 on failure.
    """
    with workspace_lock(ws) as held:
        if not held:
            say(BUSY)
            return None, EX_TEMPFAIL
        incoming = _release_manifest(release_dir)
        label = f"v{installed_version(ws)}-to-v{incoming['version']}-reinstall"
        try:
            snap = snapshot_mod.create(ws, label)
        except OSError as exc:
            say(f"Couldn't save a safety copy first ({exc}). Nothing was changed.")
            return None, 1
        try:
            return apply_release(release_dir, ws, log, mode="install", home=home), 0
        except (ApplyError, OSError, ValueError, KeyboardInterrupt) as exc:
            problem = str(exc) or "it was stopped before it finished"
        log.error("re-install failed (%s); restoring %s", problem, snap.name)
        try:
            snapshot_mod.restore_after_failed_update(ws, snap, incoming, release_dir=release_dir,
                                                     home=home, log=log)
        except Exception as exc:  # report, never raise: the user needs the message below
            log.exception("restore failed")
            say(f"Setup failed ({problem}), and putting things back failed too ({exc}).")
            say(f"Your files are saved in .kit/snapshots/{snap.name}. "
                "Please send this whole window to your consultant.")
            return None, 1
        say(f"Setup failed ({problem}). Your brain was put back exactly as it was before.")
        say("Please send this whole window to your consultant.")
        return None, 1


LABELS = {"claude": "Claude Code", "t3": "T3 Code", "gws": "the Google tool (gws)", "codex": "Codex"}


def _install_tools(ws: Workspace, home: Path, runner: Runner, log, extra: Sequence[str], say,
                   apps_dirs: Optional[Sequence[Path]]) -> Tuple[List[str], bool]:
    """Install the standard tools plus the named add-ons. Returns (the required tools that are
    still missing, whether Codex was among the tools)."""
    try:
        tools = load_tools(ws.tools_file)
    except ToolError as exc:
        say(f"  Couldn't read the tools list: {exc}")
        return ["claude"], False
    known = [t["name"] for t in tools]
    unknown = [name for name in extra if name not in known]
    if unknown:
        say(f"  Not a tool the brain knows, so skipped: {', '.join(unknown)} "
            f"(known: {', '.join(known)})")
    names = default_names(tools)
    names += [n for n in extra if n in known and n not in names]
    outcomes = install_many(tools, runner, home, log, names=names, apps_dirs=apps_dirs)
    for outcome in outcomes:
        say(f"  {outcome_line(outcome)}")
    ok = {o.name for o in outcomes if o.ok}
    missing = [t["name"] for t in tools if t.get("required") and t["name"] not in ok]
    if not missing and len(ok) < len(outcomes):
        say("Something above didn't install. Send this window to your consultant; "
            "everything else still works.")
    return missing, "codex" in names


def run_install(release_dir, ws: Workspace, home: Path, runner: Runner, log,
                no_tools: bool = False, no_path: bool = False, out=None,
                apps_dirs: Optional[Sequence[Path]] = None,
                extra_tools: Optional[Sequence[str]] = None) -> int:
    stream = out if out is not None else sys.stdout

    def say(message: str) -> None:
        print(message, file=stream)

    home = Path(home)
    where = display(ws.root, home)
    again = ws.manifest.exists()
    say(f"{'Refreshing' if again else 'Setting up'} your brain in {where} ...")
    try:
        ws.root.mkdir(parents=True, exist_ok=True)
        os.chmod(str(ws.root), 0o700)  # mail, Slack and briefs live here: owner only
        ws.ensure_dirs()
        if again:
            report, code = _apply_over_existing(release_dir, ws, home, log, say)
            if report is None:
                return code
        else:
            report = apply_release(release_dir, ws, log, mode="install", home=home)
    except (ApplyError, OSError, ValueError) as exc:
        say(f"Setup failed: {exc}")
        say("Please send this whole window to your consultant.")
        return 1
    if report.kept_modified:
        say("You had changed these kit files, so they were kept. The new version is beside each:")
        for rel in report.kept_modified:
            say(f"  {rel}.new")
    if report.codex_rules.startswith("failed"):
        say(f"Note: Codex safety rules weren't copied ({report.codex_rules}).")
    try:
        warning = link_cli(ws, home)
    except OSError as exc:
        warning = f"couldn't link the brain command ({exc})"
    if warning:
        say(f"Note: {warning}.")
    if not no_path:
        try:
            ensure_path_block(ws, home)
        except OSError as exc:
            say(f"Note: couldn't update ~/.zprofile ({exc}); add {ws.kit_bin} to your PATH.")
    codex = False
    if not no_tools:
        say("Installing the tools the brain uses (this can take a few minutes) ...")
        missing, codex = _install_tools(ws, home, runner, log, split_names(extra_tools), say,
                                        apps_dirs)
        if missing:
            say("")
            say(" and ".join(LABELS.get(n, n) for n in missing)
                + " didn't install, so your brain can't be used yet.")
            say(f"Your brain folder is in place in {where}, and running the install again is safe:")
            say("it keeps your files and picks up where it stopped.")
            return EX_TOOLS
    say(f"Your brain is ready in {where}.")
    say(next_steps(where, codex=codex))
    return 0


def register(subparsers) -> None:
    parser = subparsers.add_parser(
        "install", help="set up a brain folder from a downloaded release (the installer runs this)")
    parser.add_argument("--release-dir", required=True, help="the unpacked release folder")
    parser.add_argument("--workspace", default=argparse.SUPPRESS,
                        help="brain folder (default: $BRAIN_HOME or ~/brain)")
    parser.add_argument("--home", default=None, help="home folder (default: yours)")
    parser.add_argument("--no-tools", action="store_true", help="skip installing outside tools")
    parser.add_argument("--with", dest="extra_tools", action="append", default=[], metavar="TOOLS",
                        help="also install these add-ons, e.g. --with gws,codex (repeatable)")
    parser.add_argument("--no-path", action="store_true", help="leave ~/.zprofile alone")
    parser.set_defaults(func=run)


def run(args) -> int:
    from .log import get_logger

    home = Path(os.path.abspath(os.path.expanduser(args.home))) if args.home else Path.home()
    value = getattr(args, "workspace", None)
    if value:
        root = Path(value).expanduser()
    elif os.environ.get("BRAIN_HOME"):
        root = Workspace.default().root
    else:
        root = home / "brain"
    ws = Workspace(Path(os.path.abspath(str(root))))
    ws.root.mkdir(parents=True, exist_ok=True)
    return run_install(Path(args.release_dir), ws, home, Runner(), get_logger(ws),
                       no_tools=args.no_tools, no_path=args.no_path, extra_tools=args.extra_tools)
