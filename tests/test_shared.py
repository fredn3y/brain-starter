from __future__ import annotations

import json

from brainkit import config as config_mod
from brainkit.cli import build_parser
from brainkit.runner import FakeRunner, Result
from brainkit.runs import RunsLedger


def test_config_defaults_and_overlay(ws):
    cfg = config_mod.load(ws)
    assert cfg.timezone == "Europe/London" and cfg.brief_hour_minute == (7, 30)
    ws.config_file.write_text(json.dumps({"brief_time": "08:15", "unknown": 1}))
    cfg = config_mod.load(ws)
    assert cfg.brief_hour_minute == (8, 15)


def test_runs_ledger_last_and_torn_line(ws, at):
    runs = RunsLedger(ws)
    runs.record("brief", "error", at(2026, 10, 20, 7, 30), at(2026, 10, 20, 7, 31), detail="boom")
    runs.record("brief", "ok", at(2026, 10, 21, 9, 12), at(2026, 10, 21, 9, 14), summary="done")
    with ws.runs_file.open("a") as handle:
        handle.write('{"job": "brief", "sta')  # torn write
    assert runs.last("brief")["summary"] == "done"
    assert runs.last("brief", status="error")["detail"] == "boom"
    assert runs.last_finished("brief").hour == 9


def test_fake_runner_prefix_match():
    fake = FakeRunner().on("security", "find-generic-password", result=Result(0, "x\n"))
    assert fake.run(["security", "find-generic-password", "-s", "brain.slack"]).stdout == "x\n"
    assert fake.run(["other"]).returncode == 127
    assert fake.argvs()[0][0] == "security"


def test_cli_builds_even_with_missing_modules():
    parser = build_parser()
    assert parser.prog == "brain"
