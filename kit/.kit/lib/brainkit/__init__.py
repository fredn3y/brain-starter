"""brainkit: the Python side of the brain starter kit (stdlib only, Python 3.9+)."""

from __future__ import annotations

from pathlib import Path

_VERSION_FILE = Path(__file__).resolve().parents[2] / "VERSION"


def kit_version() -> str:
    """Version of the kit code that is running (the release's `.kit/VERSION`)."""
    try:
        return _VERSION_FILE.read_text(encoding="utf-8").strip() or "0.0.0"
    except OSError:
        return "0.0.0"
