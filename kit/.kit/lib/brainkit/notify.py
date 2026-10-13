"""Desktop notifications through `osascript` on macOS; a no-op anywhere else.

On recent macOS, `display notification` can show nothing until notifications are allowed for
"Script Editor" (System Settings → Notifications). There's no way to check that from here, so
`brain notify-test` sends one and tells the user where to look.
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from typing import Callable, Optional

from .runner import Result, Runner

MESSAGE_LIMIT = 200
TITLE_LIMIT = 60
TEST_HINT = ("If you didn't see a notification, open System Settings → Notifications → Script Editor "
             "and turn on Allow notifications.")


def applescript_text(text: str, limit: int) -> str:
    """Flatten to one line, cut to `limit` characters, then escape for an AppleScript string."""
    flat = re.sub(r"[\x00-\x1f\x7f]", " ", str(text))
    flat = re.sub(r"\s+", " ", flat).strip()
    if len(flat) > limit:
        flat = flat[: limit - 1].rstrip() + "…"
    return flat.replace("\\", "\\\\").replace('"', '\\"')


def notify(runner: Runner, title: str, message: str, platform: Optional[str] = None) -> Optional[Result]:
    """Show a notification on macOS. Returns the osascript result, or None when not on a Mac."""
    if (platform or sys.platform) != "darwin":
        return None
    body = applescript_text(message, MESSAGE_LIMIT)
    heading = applescript_text(title, TITLE_LIMIT)
    script = f'display notification "{body}" with title "{heading}"'
    return runner.run(["osascript", "-e", script], timeout=15)


def make_notifier(runner: Runner, log: logging.Logger,
                  platform: Optional[str] = None) -> Callable[[str, str], None]:
    """A `notify(title, message)` callable for jobs. It never raises."""

    def send(title: str, message: str) -> None:
        try:
            log.info("notification: %s: %s", title, message)
            result = notify(runner, title, message, platform=platform)
            if result is not None and not result.ok:
                log.warning("notification not shown (osascript exit %s)", result.returncode)
        except Exception as exc:  # a notification must never break a run
            try:
                log.warning("notification failed: %s", exc)
            except Exception:
                pass

    return send


# ---------------------------------------------------------------- CLI


def _runner() -> Runner:
    return Runner()


def _platform() -> str:
    return sys.platform


def register(subparsers) -> None:
    parser = subparsers.add_parser("notify-test", help="send a test notification")
    parser.set_defaults(func=run_test)


def run_test(args: argparse.Namespace) -> int:
    if _platform() != "darwin":
        print("Notifications only work on a Mac.", file=sys.stderr)
        return 2
    result = notify(_runner(), "Brain", "This is a test notification from your brain.", platform="darwin")
    if result is not None and not result.ok:
        print(f"The notification couldn't be sent (osascript exit {result.returncode}).", file=sys.stderr)
    print(TEST_HINT)
    return 0 if result is None or result.ok else 1
