"""Shared fixtures. Tests run on Linux; macOS commands are faked through FakeRunner."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
LIB = REPO / "kit" / ".kit" / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from brainkit.clock import tz  # noqa: E402
from brainkit.paths import Workspace  # noqa: E402
from brainkit.runner import FakeRunner  # noqa: E402


@pytest.fixture
def repo() -> Path:
    return REPO


@pytest.fixture
def ws(tmp_path) -> Workspace:
    w = Workspace(tmp_path / "brain")
    w.root.mkdir()
    w.ensure_dirs()
    return w


@pytest.fixture
def fake() -> FakeRunner:
    return FakeRunner()


def london(year, month, day, hour=0, minute=0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=tz("Europe/London"))


@pytest.fixture
def at():
    """at(2026, 10, 21, 9, 12) -> aware Europe/London datetime."""
    return london
