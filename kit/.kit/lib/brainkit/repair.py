"""`brain repair`: put the small pieces of glue back without touching the user's files.

- re-links every skill folder into `.agents/skills/` (so Codex sees a new `my-*` skill);
- re-copies the kit's Codex safety rules into `~/.codex/rules/`;
- makes sure the `brain` command and the PATH block in `~/.zprofile` are in place.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from .paths import Workspace


def run_repair(ws: Workspace, home: Optional[Path] = None) -> List[str]:
    from .apply import rebuild_links, sync_codex_rules
    from .install import ensure_path_block, link_cli

    home = Path(home) if home is not None else Path.home()
    lines = []
    linked = rebuild_links(ws)
    lines.append(f"skills linked for Codex: {len(linked)}")
    rules = sync_codex_rules(ws, home)
    lines.append(f"Codex safety rules: {rules or 'not in this kit version'}")
    try:
        link_result = link_cli(ws, home)
        lines.append(f"brain command: {link_result or 'ok'}")
    except OSError as exc:
        lines.append(f"brain command: couldn't link ({exc})")
    try:
        ensure_path_block(ws, home)
        lines.append("PATH block in ~/.zprofile: ok")
    except OSError as exc:
        lines.append(f"PATH block in ~/.zprofile: couldn't write ({exc})")
    return lines


def register(subparsers) -> None:
    parser = subparsers.add_parser("repair", help="re-link skills, re-copy safety rules, fix the PATH")
    parser.set_defaults(func=run)


def run(args) -> int:
    ws = Workspace(Path(args.workspace)) if getattr(args, "workspace", None) else Workspace.default()
    for line in run_repair(ws):
        print(line)
    return 0
