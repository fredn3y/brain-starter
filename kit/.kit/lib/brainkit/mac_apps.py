"""Mac apps: find an installed .app by name pattern, and read an app's own update feed.

Apps built with electron-builder (T3 Code is one) publish `latest-mac.yml` beside each release:

    version: 0.1.9
    files:
      - url: T3-Code-0.1.9-arm64.zip
        sha512: <base64>
        size: 123
      - url: T3-Code-0.1.9-x64.zip
        ...

`feed_asset` reads that small file without a YAML library and returns the https download for
this Mac plus its sha512 (as hex, for tools._verify). Anything unexpected is refused. The app
updates itself after that, so an installed copy matching the pattern is never replaced.
"""

from __future__ import annotations

import base64
import binascii
import fnmatch
import os
import plistlib
import re
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

SYSTEM_APPS = Path("/Applications")
T3_APP_GLOB = "T3 Code*.app"  # "T3 Code (Alpha).app", "T3 Code (Beta).app", whatever comes next
_PLAIN_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_VERSION = re.compile(r"^\d+(\.\d+){0,3}([-+][0-9A-Za-z.-]+)?$")


class FeedError(ValueError):
    """The update feed is unreadable, incomplete or points somewhere we won't download from."""


# ---------------------------------------------------------------- installed apps


def apps_dirs(home: Path, dirs: Optional[Sequence[Path]] = None) -> List[Path]:
    """Where apps live: /Applications, then ~/Applications (or the given folders, for tests)."""
    if dirs is not None:
        return [Path(p) for p in dirs]
    return [SYSTEM_APPS, Path(home) / "Applications"]


def find_apps(pattern: str, home: Path, dirs: Optional[Sequence[Path]] = None) -> List[Path]:
    """Every installed app whose folder name matches `pattern` (an exact name or a glob)."""
    found: List[Path] = []
    if not pattern or "/" in pattern or not pattern.endswith(".app"):
        return found
    for folder in apps_dirs(home, dirs):
        try:
            names = sorted(os.listdir(str(folder)))
        except OSError:
            continue
        for name in names:
            if fnmatch.fnmatchcase(name, pattern) and (folder / name).is_dir():
                found.append(folder / name)
    return found


def find_app(pattern: str, home: Path, dirs: Optional[Sequence[Path]] = None) -> Optional[Path]:
    apps = find_apps(pattern, home, dirs)
    return apps[0] if apps else None


def apps_target(home: Path, dirs: Optional[Sequence[Path]] = None) -> Path:
    """/Applications when it is writable, else ~/Applications (made if missing)."""
    folders = apps_dirs(home, dirs)
    system, user = folders[0], folders[-1]
    if system.is_dir() and os.access(str(system), os.W_OK):
        return system
    user.mkdir(parents=True, exist_ok=True)
    return user


def remove_tree(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(str(path))


def app_version(app: Path) -> str:
    try:
        with open(str(app / "Contents" / "Info.plist"), "rb") as handle:
            info = plistlib.load(handle)
        return str(info.get("CFBundleShortVersionString") or info.get("CFBundleVersion") or "")
    except Exception:  # a broken plist must not break `brain tools status` or doctor
        return ""


# ---------------------------------------------------------------- update feed


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1].replace("''", "'")
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return value[1:-1]
    return value


def parse_feed(text: str) -> Tuple[str, List[Dict[str, str]]]:
    """(version, [{"url", "sha512", "size"}, …]) from a latest-mac.yml. Only the top-level
    `version` and the `files` list are read; everything else is ignored."""
    version = ""
    files: List[Dict[str, str]] = []
    in_files = False
    for raw in (text or "").splitlines():
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        top_level = not raw[:1].isspace() and not line.startswith("-")
        if top_level:
            key, _, value = line.partition(":")
            in_files = key.strip() == "files" and not value.strip()
            if key.strip() == "version":
                version = _unquote(value)
            continue
        if not in_files:
            continue
        item = line.strip()
        if item.startswith("- "):
            files.append({})
            item = item[2:].strip()
        if not files:
            continue
        key, sep, value = item.partition(":")
        if sep:
            files[-1][key.strip()] = _unquote(value)
    return version, files


def sha512_hex(value: str) -> str:
    """electron-builder writes sha512 as base64; tools.json and _verify use hex."""
    try:
        raw = base64.b64decode(value.strip(), validate=True)
    except (binascii.Error, ValueError):
        raise FeedError("the update feed has an unreadable sha512") from None
    if len(raw) != 64:
        raise FeedError("the update feed has a sha512 of the wrong length")
    return raw.hex()


def download_url(feed_url: str, name: str) -> str:
    """A feed entry's download address: a plain file name sits beside the feed; a full address
    must be https."""
    if name.startswith("https://"):
        return name
    if "://" in name or not _PLAIN_NAME.match(name):
        raise FeedError(f"the update feed points at a download we won't use ({name[:80]!r})")
    return feed_url.rsplit("/", 1)[0] + "/" + name


def feed_asset(text: str, feed_url: str, suffix: str) -> Tuple[str, str, Dict[str, str]]:
    """(version, https download url, {"sha512": hex}) for the feed entry ending in `suffix`."""
    if not feed_url.startswith("https://"):
        raise FeedError("refusing an update feed that isn't https")
    version, files = parse_feed(text)
    if not _VERSION.match(version):
        raise FeedError("the update feed doesn't say which version is current")
    if not suffix:
        raise FeedError("no download is listed for this Mac")
    for entry in files:
        name = entry.get("url", "")
        if name.endswith(suffix):
            if not entry.get("sha512"):
                raise FeedError(f"the update feed has no checksum for {name}")
            return version, download_url(feed_url, name), {"sha512": sha512_hex(entry["sha512"])}
    raise FeedError(f"the update feed lists no download ending in {suffix}")
