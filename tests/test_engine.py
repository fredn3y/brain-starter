"""engine.py: the headless `claude -p` command, its environment, output parsing and logs."""

from __future__ import annotations

import json
import logging
from dataclasses import replace
from pathlib import Path

import pytest

from brainkit import engine
from brainkit.config import Config
from brainkit.jobs.base import JobContext
from brainkit.runner import FakeRunner, Result
from brainkit.runs import RunsLedger

OK = {"type": "result", "subtype": "success", "is_error": False, "result": "Wrote the brief.",
      "total_cost_usd": 0.1234, "session_id": "s1"}


@pytest.fixture
def claude(tmp_path) -> Path:
    path = tmp_path / "bin" / "claude"
    path.parent.mkdir()
    path.write_text("#!/bin/sh\n")
    path.chmod(0o755)
    return path


def file_logger(ws) -> logging.Logger:
    logger = logging.getLogger(f"test-engine-{ws.root}")
    logger.handlers = []
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = logging.FileHandler(str(ws.logs / "brain.log"), encoding="utf-8")
    logger.addHandler(handler)
    return logger


def make_ctx(ws, runner, at, claude, **env_extra):
    env = {"PATH": str(claude.parent), "HOME": str(claude.parent.parent / "home"), "USER": "tester"}
    env.update(env_extra)
    return JobContext(ws=ws, config=Config(), runner=runner, now=at(2026, 10, 21, 9, 12), runs=RunsLedger(ws),
                      log=file_logger(ws), notify=lambda title, message: None, env=env)


def no_token(runner: FakeRunner) -> FakeRunner:
    return runner.on("security", "find-generic-password", "-s", "brain.claude-oauth-token", result=Result(44))


def test_argv_order_cwd_and_tools(ws, at, claude):
    runner = no_token(FakeRunner()).on(str(claude), result=Result(0, json.dumps(OK)))
    ctx = make_ctx(ws, runner, at, claude)
    result = engine.run_skill(ctx, "morning-brief", "Data packet: briefs/.packets/2026-10-21.json",
                              engine.DEFAULT_TOOLS)
    call = runner.calls[-1]
    assert call["argv"] == [
        str(claude), "-p", "/morning-brief\n\nData packet: briefs/.packets/2026-10-21.json",
        "--permission-mode", "dontAsk", "--permission-prompts", "none", "--output-format", "json",
        "--max-turns", "40", "--model", "sonnet", "--tools", "Read,Glob,Grep,Edit,Write",
        "--allowedTools", "Read", "Glob", "Grep", "Edit(/briefs/**)"]
    assert "Bash" not in engine.AVAILABLE_TOOLS  # the scheduled run reads untrusted text: no shell
    assert call["cwd"] == str(ws.root)
    assert result.ok and result.returncode == 0 and result.result_text == "Wrote the brief."
    assert result.cost_usd == pytest.approx(0.1234)


def test_model_comes_from_settings(ws, at, claude):
    runner = no_token(FakeRunner()).on(str(claude), result=Result(0, json.dumps(OK)))
    ctx = make_ctx(ws, runner, at, claude)
    ctx.config = replace(ctx.config, brief_model="opus")
    engine.run_skill(ctx, "weekly-review", "x", ["Read"], max_turns=12)
    argv = runner.calls[-1]["argv"]
    assert argv[argv.index("--model") + 1] == "opus" and argv[argv.index("--max-turns") + 1] == "12"
    assert argv[-2:] == ["--allowedTools", "Read"]


def test_token_is_added_only_when_the_secret_exists(ws, at, claude):
    runner = no_token(FakeRunner()).on(str(claude), result=Result(0, json.dumps(OK)))
    engine.run_skill(make_ctx(ws, runner, at, claude, CLAUDECODE="1"), "morning-brief", "x", engine.DEFAULT_TOOLS)
    env = runner.calls[-1]["env"]
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in env and "CLAUDECODE" not in env
    assert env["BRAIN_ENGINE_RUN"] == "1"  # keeps the session-start hook quiet in scheduled runs
    assert env["USER"] == "tester" and env["HOME"].endswith("home") and env["PATH"] == str(claude.parent)

    runner = FakeRunner().on("security", "find-generic-password", "-s", "brain.claude-oauth-token",
                             result=Result(0, "sk-ant-oat01-example-token-value\n"))
    runner.on(str(claude), result=Result(0, json.dumps(OK)))
    result = engine.run_skill(make_ctx(ws, runner, at, claude), "morning-brief", "x", engine.DEFAULT_TOOLS)
    assert runner.calls[-1]["env"]["CLAUDE_CODE_OAUTH_TOKEN"] == "sk-ant-oat01-example-token-value"
    assert "sk-ant-oat01" not in result.log_path.read_text()
    assert "sk-ant-oat01" not in (ws.logs / "brain.log").read_text()


def test_a_keychain_problem_just_means_no_token(ws, at, claude):
    runner = FakeRunner().on("security", result=Result(51, "", "locked")).on(str(claude), result=Result(0, json.dumps(OK)))
    result = engine.run_skill(make_ctx(ws, runner, at, claude), "morning-brief", "x", engine.DEFAULT_TOOLS)
    assert result.ok and "CLAUDE_CODE_OAUTH_TOKEN" not in runner.calls[-1]["env"]


@pytest.mark.parametrize("message", [
    "Failed to authenticate: OAuth session expired and could not be refreshed",
    "Not logged in · Please run /login",
])
def test_expired_login_gives_the_hint(ws, at, claude, message):
    out = json.dumps({"type": "result", "subtype": "success", "is_error": True, "result": message})
    runner = no_token(FakeRunner()).on(str(claude), result=Result(1, out))
    result = engine.run_skill(make_ctx(ws, runner, at, claude), "morning-brief", "x", engine.DEFAULT_TOOLS)
    assert not result.ok and result.reason == engine.AUTH_HINT


def test_success_that_mentions_login_is_still_success(ws, at, claude):
    out = dict(OK, result="Reminder from Slack: please run /login on the shared laptop")
    runner = no_token(FakeRunner()).on(str(claude), result=Result(0, json.dumps(out)))
    assert engine.run_skill(make_ctx(ws, runner, at, claude), "morning-brief", "x", engine.DEFAULT_TOOLS).ok


@pytest.mark.parametrize("returncode,stdout,stderr,expected", [
    (0, "not json at all", "", "no readable answer"),
    (1, json.dumps({"type": "result", "subtype": "error_max_turns", "is_error": True}), "", "ran out of steps"),
    (1, json.dumps({"type": "result", "subtype": "error_during_execution", "is_error": True}), "",
     "error_during_execution"),
    (0, json.dumps(dict(OK, is_error=True)), "", "reported an error"),
    (127, "", "command not found: claude", "isn't installed"),
    (124, "", "timed out after 900s: claude", "took longer than 15 minutes"),
])
def test_failures_have_short_content_free_reasons(ws, at, claude, returncode, stdout, stderr, expected):
    runner = no_token(FakeRunner()).on(str(claude), result=Result(returncode, stdout, stderr))
    result = engine.run_skill(make_ctx(ws, runner, at, claude), "morning-brief", "x", engine.DEFAULT_TOOLS)
    assert not result.ok and expected in result.reason
    assert "not json" not in result.reason


def test_raw_output_goes_to_the_engine_log_only(ws, at, claude):
    out = dict(OK, result="Private brief text about the quarterly numbers")
    runner = no_token(FakeRunner()).on(str(claude), result=Result(0, json.dumps(out), "a warning"))
    result = engine.run_skill(make_ctx(ws, runner, at, claude), "morning-brief", "x", engine.DEFAULT_TOOLS)
    assert result.log_path == ws.logs / "engine" / "morning-brief-2026-10-21.log"
    log_text = result.log_path.read_text()
    assert "quarterly numbers" in log_text and "a warning" in log_text
    assert (result.log_path.stat().st_mode & 0o777) == 0o600
    brain_log = (ws.logs / "brain.log").read_text()
    assert "engine morning-brief: ok ($0.12)" in brain_log
    assert "quarterly numbers" not in brain_log and "a warning" not in brain_log


def test_find_claude_prefers_path_then_the_native_install(tmp_path, claude):
    assert engine.find_claude(tmp_path / "home", str(claude.parent)) == str(claude)
    empty = tmp_path / "empty"
    empty.mkdir()
    assert engine.find_claude(tmp_path / "home", str(empty)) == str(tmp_path / "home" / ".local" / "bin" / "claude")


def test_other_engines_are_refused_cleanly(ws, at, claude):
    runner = no_token(FakeRunner())
    ctx = make_ctx(ws, runner, at, claude)
    ctx.config = replace(ctx.config, engine="codex")
    result = engine.run_skill(ctx, "morning-brief", "x", engine.DEFAULT_TOOLS)
    assert not result.ok and "isn't supported" in result.reason and runner.calls == []
