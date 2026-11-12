"""`brain` command line. Each command module registers its own subcommand(s).

To add a command: create a module with `register(subparsers)` that sets
`parser.set_defaults(func=run)` where `run(args) -> int`, then add it to COMMAND_MODULES.
"""

from __future__ import annotations

import argparse
import importlib
import sys
from typing import List, Optional

from . import kit_version

COMMAND_MODULES = [
    "doctor",
    "catchup",
    "notify",
    "brief",
    "launchagent",
    "secrets",
    "slack_cli",
    "google_cli",
    "report",
    "update",
    "apply",
    "install",
    "tools",
    "repair",
    "session_start",
    "trash",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="brain", description="Your brain's helper commands.")
    parser.add_argument("--version", action="version", version=f"brain {kit_version()}")
    parser.add_argument("--workspace", help="brain folder (default: $BRAIN_HOME or ~/brain)")
    sub = parser.add_subparsers(dest="command", metavar="<command>")
    for name in COMMAND_MODULES:
        try:
            module = importlib.import_module(f"brainkit.{name}")
        except ImportError as exc:  # keep the CLI usable if one module is broken
            broken = sub.add_parser(name, help=f"(unavailable: {exc})")
            broken.set_defaults(func=lambda args, _e=exc: _unavailable(_e))
            continue
        register = getattr(module, "register", None)
        if register is not None:
            register(sub)
    return parser


def _unavailable(exc: Exception) -> int:
    print(f"This command is unavailable: {exc}. Run `brain doctor`.", file=sys.stderr)
    return 2


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return 0
    return int(func(args) or 0)
