"""Open a link for the user on macOS: in Google Chrome when it is installed (the setup prompts
run in Claude in Chrome, and the user's work Google account is usually signed in there), else
in the default browser. Anywhere else it does nothing; callers always print the link too.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Callable, Optional

from .runner import Runner

CHROME = "Google Chrome"


def chrome_installed(home: Any = None, exists: Callable[[str], bool] = os.path.isdir) -> bool:
    places = (Path("/Applications"), Path(str(home or Path.home())) / "Applications")
    return any(exists(str(place / f"{CHROME}.app")) for place in places)


def open_url(runner: Runner, url: str, platform: Optional[str] = None, home: Any = None) -> bool:
    """Ask macOS to open `url`. True when `open` succeeded; False elsewhere or on failure."""
    if (platform or sys.platform) != "darwin":
        return False
    if chrome_installed(home):
        if runner.run(["open", "-a", CHROME, url], timeout=30).ok:
            return True
    return runner.run(["open", url], timeout=30).ok
