"""The scheduled health job: quick doctor checks, problems kept for the brief."""

from __future__ import annotations

import logging

import pytest

from brainkit import doctor
from brainkit import jobs as jobs_pkg
from brainkit.config import Config
from brainkit.doctor import Check
from brainkit.jobs import health
from brainkit.jobs.base import JobContext
from brainkit.runner import FakeRunner
from brainkit.runs import RunsLedger


@pytest.fixture
def ctx(ws, at):
    return JobContext(ws=ws, config=Config(), runner=FakeRunner(), now=at(2026, 10, 20, 9, 12),
                      runs=RunsLedger(ws), log=logging.getLogger("test-health"), notify=lambda t, m: None,
                      env={"PATH": "/usr/bin:/bin", "HOME": "/Users/someone"})


def fake_checks(monkeypatch, checks):
    seen = {}

    def run_checks(*args, **kwargs):
        seen["args"], seen["kwargs"] = args, kwargs
        return checks

    monkeypatch.setattr(doctor, "run_checks", run_checks)
    return seen


def test_all_good(ctx, monkeypatch):
    fake_checks(monkeypatch, [Check("macos", "ok", "macOS 14.5"), Check("cpu", "info", "arm64")])
    result = health.run(ctx)
    assert result.status == "ok" and result.summary == "all good" and result.data == {"problems": []}


def test_problems_are_listed_for_the_brief(ctx, monkeypatch):
    fake_checks(monkeypatch, [
        Check("macos", "ok", "macOS 14.5"),
        Check("slack", "warn", "Slack isn't connected", "run `brain secret set slack --gui`"),
        Check("google", "fail", "Google isn't connected", "run `brain google connect`"),
        Check("mdm", "info", "MDM enrollment: No"),
    ])
    result = health.run(ctx)
    assert result.status == "ok" and result.summary == "2 problems"
    assert result.data["problems"] == [
        {"name": "slack", "status": "warn", "message": "Slack isn't connected",
         "fix": "run `brain secret set slack --gui`"},
        {"name": "google", "status": "fail", "message": "Google isn't connected", "fix": "run `brain google connect`"},
    ]


def test_single_problem_wording(ctx, monkeypatch):
    fake_checks(monkeypatch, [Check("disk", "warn", "only 1.0 GB free", "free up space")])
    assert health.run(ctx).summary == "1 problem"


def test_runs_quick_checks_with_the_job_environment(ctx, monkeypatch):
    seen = fake_checks(monkeypatch, [])
    health.run(ctx)
    assert seen["args"][:3] == (ctx.ws, ctx.config, ctx.runner)
    assert seen["kwargs"]["quick"] is True
    assert seen["kwargs"]["env"] == ctx.env and seen["kwargs"]["now"] == ctx.now


def test_exceptions_reach_the_runner(ctx, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("doctor broke")

    monkeypatch.setattr(doctor, "run_checks", broken)
    with pytest.raises(RuntimeError):
        health.run(ctx)


def test_registered_as_a_job():
    assert jobs_pkg.job_runner("health") is health.run


def test_real_checks_with_nothing_installed(ctx, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    result = health.run(ctx)  # FakeRunner answers 127 for every command
    assert result.status == "ok"
    names = {problem["name"] for problem in result.data["problems"]}
    assert "claude" in names  # required: a problem
    assert "google" not in names  # an add-on that was never set up: not a problem
    assert all(set(p) == {"name", "status", "message", "fix"} for p in result.data["problems"])
