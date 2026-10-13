"""Due-job computation: slots, intervals, missed days, DST, retries, lookback, work hours."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from brainkit import schedule
from brainkit.config import Config
from brainkit.runs import RunsLedger

UTC = timezone.utc


def names(due):
    return [item.name for item in due]


def by_name(due):
    return {item.name: item for item in due}


def ok(runs, job, finished, minutes=2):
    # No window data in `data`, so the next window resumes at started_at (finished - minutes).
    runs.record(job, "ok", finished - timedelta(minutes=minutes), finished)


def fail(runs, job, started, status="error"):
    runs.record(job, status, started, started + timedelta(minutes=1), detail="boom")


def settled_ledger(ws, at, brief_day=(2026, 10, 19)):
    """Everything ran on Monday 19 Oct (brief at 07:31, weekly at 07:35)."""
    runs = RunsLedger(ws)
    ok(runs, "brief", at(*brief_day, 7, 31))
    ok(runs, "weekly_review", at(2026, 10, 19, 7, 35))
    ok(runs, "health", at(2026, 10, 20, 6, 0))
    ok(runs, "slack_sync", at(2026, 10, 19, 17, 30))
    ok(runs, "update_check", at(2026, 10, 20, 6, 0))
    return runs


# ---------------------------------------------------------------- latest_slot


def test_latest_slot_weekday_before_at_and_after(at):
    weekdays = [1, 2, 3, 4, 5]
    assert schedule.latest_slot(at(2026, 10, 20, 7, 0), "07:30", weekdays, "Europe/London") == at(2026, 10, 19, 7, 30)
    assert schedule.latest_slot(at(2026, 10, 20, 7, 30), "07:30", weekdays, "Europe/London") == at(2026, 10, 20, 7, 30)
    assert schedule.latest_slot(at(2026, 10, 20, 7, 31), "07:30", weekdays, "Europe/London") == at(2026, 10, 20, 7, 30)


def test_latest_slot_weekend_and_monday_morning_point_at_friday(at):
    weekdays = [1, 2, 3, 4, 5]
    friday = at(2026, 10, 16, 7, 30)
    for now in (at(2026, 10, 17, 10, 0), at(2026, 10, 18, 23, 59), at(2026, 10, 19, 7, 29)):
        assert schedule.latest_slot(now, "07:30", weekdays, "Europe/London") == friday


def test_latest_slot_none_without_weekdays(at):
    assert schedule.latest_slot(at(2026, 10, 20, 9, 0), "07:30", [], "Europe/London") is None


def test_latest_slot_spring_forward_week_uses_local_wall_time(at):
    # Clocks went forward on Sun 29 Mar 2026. Mon 07:30 BST is 06:30 UTC.
    slot = schedule.latest_slot(at(2026, 3, 30, 7, 45), "07:30", [1, 2, 3, 4, 5], "Europe/London")
    assert slot.astimezone(UTC) == datetime(2026, 3, 30, 6, 30, tzinfo=UTC)
    before = schedule.latest_slot(at(2026, 3, 30, 7, 15), "07:30", [1, 2, 3, 4, 5], "Europe/London")
    assert before.astimezone(UTC) == datetime(2026, 3, 27, 7, 30, tzinfo=UTC)  # Friday, still GMT


def test_latest_slot_fall_back_week_uses_local_wall_time(at):
    # Clocks went back on Sun 25 Oct 2026. Mon 07:30 GMT is 07:30 UTC.
    before = schedule.latest_slot(at(2026, 10, 26, 7, 15), "07:30", [1, 2, 3, 4, 5], "Europe/London")
    assert before.astimezone(UTC) == datetime(2026, 10, 23, 6, 30, tzinfo=UTC)  # Friday, BST
    after = schedule.latest_slot(at(2026, 10, 26, 7, 45), "07:30", [1, 2, 3, 4, 5], "Europe/London")
    assert after.astimezone(UTC) == datetime(2026, 10, 26, 7, 30, tzinfo=UTC)


def test_latest_slot_inside_spring_gap_becomes_just_after_it(at):
    # 01:30 doesn't exist on Sun 29 Mar 2026 in London; the slot fires at 02:30 BST.
    now = datetime(2026, 3, 29, 2, 0, tzinfo=UTC)  # 03:00 BST
    slot = schedule.latest_slot(now, "01:30", [7], "Europe/London")
    assert slot.astimezone(UTC) == datetime(2026, 3, 29, 1, 30, tzinfo=UTC)
    assert slot.strftime("%H:%M") == "02:30"


def test_latest_slot_ambiguous_autumn_time_is_first_occurrence():
    # 01:30 happens twice on Sun 25 Oct 2026; the first (BST) one is 00:30 UTC.
    second_pass = datetime(2026, 10, 25, 1, 15, tzinfo=UTC)  # 01:15 GMT
    slot = schedule.latest_slot(second_pass, "01:30", [7], "Europe/London")
    assert slot.astimezone(UTC) == datetime(2026, 10, 25, 0, 30, tzinfo=UTC)
    first_pass = datetime(2026, 10, 25, 0, 15, tzinfo=UTC)  # 01:15 BST
    earlier = schedule.latest_slot(first_pass, "01:30", [7], "Europe/London")
    assert earlier.date() == datetime(2026, 10, 18).date()


def test_latest_slot_respects_other_time_zones():
    now = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)  # 08:00 in New York (EDT)
    slot = schedule.latest_slot(now, "07:30", [1, 2, 3, 4, 5], "America/New_York")
    assert slot.astimezone(UTC) == datetime(2026, 10, 20, 11, 30, tzinfo=UTC)


@pytest.mark.parametrize("bad", ["7.30", "730", "24:00", "07:60", "07:3", "", "soon"])
def test_parse_hhmm_rejects_bad_values(bad):
    with pytest.raises(ValueError):
        schedule.parse_hhmm(bad)


def test_parse_hhmm_accepts_single_digit_hour():
    assert schedule.parse_hhmm("7:05") == (7, 5)


# ---------------------------------------------------------------- due_jobs


def test_first_ever_run_runs_everything_in_order(ws, at):
    now = at(2026, 10, 19, 9, 12)  # Monday: a first-ever run on a Monday includes the weekly review
    due = schedule.due_jobs(Config(), RunsLedger(ws), now)
    assert names(due) == ["health", "slack_sync", "update_check", "brief", "weekly_review"]
    jobs = by_name(due)
    assert jobs["brief"].since == now - timedelta(hours=24)
    assert jobs["weekly_review"].since == now - timedelta(days=7)
    assert jobs["health"].since is None and jobs["health"].reason == "never run"


def test_weekday_morning_before_slot_nothing_due(ws, at):
    runs = settled_ledger(ws, at)
    assert schedule.due_jobs(Config(), runs, at(2026, 10, 20, 7, 10)) == []


def test_weekday_morning_after_slot_brief_with_helpers(ws, at):
    runs = settled_ledger(ws, at)
    due = schedule.due_jobs(Config(), runs, at(2026, 10, 20, 7, 31))
    assert names(due) == ["health", "slack_sync", "brief"]
    jobs = by_name(due)
    assert jobs["brief"].since == at(2026, 10, 19, 7, 29)  # the last brief's started_at
    assert jobs["health"].reason == "runs before the brief"
    assert jobs["slack_sync"].reason == "runs before the brief"


def test_long_weekend_asleep_gives_one_brief_since_friday(ws, at):
    runs = RunsLedger(ws)
    ok(runs, "weekly_review", at(2026, 10, 12, 7, 35))
    ok(runs, "brief", at(2026, 10, 16, 7, 31))
    ok(runs, "update_check", at(2026, 10, 16, 9, 0))
    ok(runs, "health", at(2026, 10, 16, 16, 0))
    ok(runs, "slack_sync", at(2026, 10, 16, 16, 59))
    # Asleep from Fri 17:00 until Tue 09:12 (Monday's slot was missed entirely).
    due = schedule.due_jobs(Config(), runs, at(2026, 10, 20, 9, 12))
    assert names(due) == ["health", "slack_sync", "update_check", "brief", "weekly_review"]
    assert names(due).count("brief") == 1
    jobs = by_name(due)
    assert jobs["brief"].since == at(2026, 10, 16, 7, 29)
    assert jobs["weekly_review"].since == at(2026, 10, 12, 7, 33)


def test_saturday_wake_runs_only_interval_jobs(ws, at):
    runs = RunsLedger(ws)
    ok(runs, "brief", at(2026, 10, 16, 7, 31))
    ok(runs, "weekly_review", at(2026, 10, 12, 7, 35))
    ok(runs, "health", at(2026, 10, 16, 16, 0))
    ok(runs, "slack_sync", at(2026, 10, 16, 16, 59))
    ok(runs, "update_check", at(2026, 10, 16, 9, 0))
    due = schedule.due_jobs(Config(), runs, at(2026, 10, 17, 10, 0))
    assert names(due) == ["health", "update_check"]


def test_monday_morning_after_weekend_runs_brief_and_weekly(ws, at):
    runs = RunsLedger(ws)
    ok(runs, "brief", at(2026, 10, 16, 7, 31))
    ok(runs, "weekly_review", at(2026, 10, 12, 7, 35))
    for job in ("health", "slack_sync", "update_check"):
        ok(runs, job, at(2026, 10, 19, 7, 0))
    due = schedule.due_jobs(Config(), runs, at(2026, 10, 19, 7, 30))
    assert names(due) == ["health", "slack_sync", "brief", "weekly_review"]
    assert by_name(due)["brief"].since == at(2026, 10, 16, 7, 29)


def test_retry_cap_waits_for_next_slot(ws, at):
    runs = settled_ledger(ws, at)
    fail(runs, "brief", at(2026, 10, 20, 7, 0))  # before the slot: doesn't count
    fail(runs, "brief", at(2026, 10, 20, 7, 30))
    fail(runs, "brief", at(2026, 10, 20, 8, 0))
    due = by_name(schedule.due_jobs(Config(), runs, at(2026, 10, 20, 8, 30)))
    assert "brief" in due and "attempt 3 of 3" in due["brief"].reason
    fail(runs, "brief", at(2026, 10, 20, 9, 0))
    assert "brief" not in names(schedule.due_jobs(Config(), runs, at(2026, 10, 20, 10, 0)))
    assert "brief" in names(schedule.due_jobs(Config(), runs, at(2026, 10, 21, 7, 31)))


def test_skipped_attempts_count_toward_retry_cap(ws, at):
    runs = settled_ledger(ws, at)
    for hour in (7, 8, 9):
        fail(runs, "brief", at(2026, 10, 20, hour, 31), status="skipped")
    assert "brief" not in names(schedule.due_jobs(Config(), runs, at(2026, 10, 20, 10, 31)))


def test_lookback_cap_limits_since(ws, at):
    runs = RunsLedger(ws)
    ok(runs, "brief", at(2026, 9, 1, 7, 31))
    now = at(2026, 10, 20, 9, 12)
    assert by_name(schedule.due_jobs(Config(), runs, now))["brief"].since == now - timedelta(days=14)
    short = Config(lookback_days_max=3)
    assert by_name(schedule.due_jobs(short, runs, now))["brief"].since == now - timedelta(days=3)


def test_weekly_since_is_capped_too(ws, at):
    now = at(2026, 10, 19, 7, 30)
    due = by_name(schedule.due_jobs(Config(lookback_days_max=5), RunsLedger(ws), now))
    assert due["weekly_review"].since == now - timedelta(days=5)


def test_weekly_review_off(ws, at):
    due = schedule.due_jobs(Config(weekly_review=False), RunsLedger(ws), at(2026, 10, 19, 9, 0))
    assert "weekly_review" not in names(due)


@pytest.mark.parametrize("hour, minute, last_minutes_ago, expected", [
    (8, 0, 61, True),     # start of work hours, stale
    (7, 59, 120, False),  # before work hours
    (12, 0, 30, False),   # fresh enough
    (12, 0, 60, True),    # exactly the interval
    (17, 59, 61, True),   # last minute of work hours
    (18, 0, 61, False),   # end is exclusive
])
def test_slack_sync_work_hours_rule(ws, at, hour, minute, last_minutes_ago, expected):
    runs = settled_ledger(ws, at, brief_day=(2026, 10, 20))  # brief already done Tuesday
    now = at(2026, 10, 20, hour, minute)
    ok(runs, "slack_sync", now - timedelta(minutes=last_minutes_ago))
    assert ("slack_sync" in names(schedule.due_jobs(Config(), runs, now))) is expected


def test_slack_sync_never_on_non_workday_without_brief(ws, at):
    runs = RunsLedger(ws)
    ok(runs, "brief", at(2026, 10, 16, 7, 31))
    assert "slack_sync" not in names(schedule.due_jobs(Config(), runs, at(2026, 10, 17, 12, 0)))


def test_health_interval(ws, at):
    runs = settled_ledger(ws, at, brief_day=(2026, 10, 20))
    assert "health" not in names(schedule.due_jobs(Config(), runs, at(2026, 10, 20, 11, 59)))
    due = by_name(schedule.due_jobs(Config(), runs, at(2026, 10, 20, 12, 0)))
    assert due["health"].reason.startswith("last ok 6 h ago")


def test_update_check_interval(ws, at):
    runs = settled_ledger(ws, at, brief_day=(2026, 10, 20))
    assert "update_check" not in names(schedule.due_jobs(Config(), runs, at(2026, 10, 21, 5, 59)))
    assert "update_check" in names(schedule.due_jobs(Config(), runs, at(2026, 10, 21, 6, 0)))


def test_dst_spring_week_brief(ws, at):
    runs = RunsLedger(ws)
    ok(runs, "brief", at(2026, 3, 27, 7, 31))  # Friday, GMT
    for job in ("health", "slack_sync", "update_check", "weekly_review"):
        ok(runs, job, at(2026, 3, 30, 7, 0))
    assert schedule.due_jobs(Config(), runs, at(2026, 3, 30, 7, 15)) == []
    due = by_name(schedule.due_jobs(Config(), runs, at(2026, 3, 30, 7, 45)))
    assert due["brief"].since == at(2026, 3, 27, 7, 29)


def test_dst_autumn_week_brief(ws, at):
    runs = RunsLedger(ws)
    ok(runs, "brief", at(2026, 10, 23, 7, 31))  # Friday, BST
    for job in ("health", "slack_sync", "update_check", "weekly_review"):
        ok(runs, job, at(2026, 10, 26, 7, 0))
    assert schedule.due_jobs(Config(), runs, at(2026, 10, 26, 7, 15)) == []
    due = by_name(schedule.due_jobs(Config(), runs, at(2026, 10, 26, 7, 45)))
    assert due["brief"].since == at(2026, 10, 23, 7, 29)


def test_interval_measured_in_real_hours_across_dst(ws, at):
    runs = RunsLedger(ws)
    ok(runs, "brief", at(2026, 10, 23, 7, 31))
    ok(runs, "update_check", at(2026, 10, 25, 5, 0))
    ok(runs, "health", datetime(2026, 10, 24, 23, 30, tzinfo=UTC))  # 00:30 BST on the 25th
    now = datetime(2026, 10, 25, 5, 45, tzinfo=UTC)  # 05:45 GMT: 6h15m real, 5h15m on the wall
    assert "health" in names(schedule.due_jobs(Config(), runs, now))


def test_rows_stamped_in_the_future_are_ignored(ws, at):
    runs = RunsLedger(ws)
    ok(runs, "brief", at(2030, 1, 1, 7, 31))
    assert "brief" in names(schedule.due_jobs(Config(), runs, at(2026, 10, 20, 9, 0)))


def test_due_jobs_accepts_plain_rows(at):
    rows = [{"job": "brief", "status": "ok", "started_at": "2026-10-20T07:30:00+01:00",
             "finished_at": "2026-10-20T07:31:00+01:00"},
            {"job": "health", "status": "ok", "started_at": "2026-10-20T08:00:00+01:00",
             "finished_at": "2026-10-20T08:01:00+01:00"}]
    due = schedule.due_jobs(Config(weekly_review=False), rows, at(2026, 10, 20, 9, 0))
    assert names(due) == ["slack_sync", "update_check"]


def test_window_since_for_requested_runs(ws, at):
    runs = RunsLedger(ws)
    now = at(2026, 10, 20, 15, 0)
    assert schedule.window_since(Config(), runs, "brief", now) == now - timedelta(hours=24)
    ok(runs, "brief", at(2026, 10, 20, 7, 31))
    assert schedule.window_since(Config(), runs, "brief", now) == at(2026, 10, 20, 7, 29)
    assert schedule.window_since(Config(), runs, "health", now) is None


# ---------------------------------------------------------------- settings


def test_sanitize_replaces_bad_values_and_explains():
    bad = Config(timezone="Mars/Base", brief_time="7.30am", workdays=[0, 8], work_hours=[18, 8],
                 slack_sync_minutes=0, health_hours="six", weekly_review="yes")
    cfg, problems = schedule.sanitize(bad)
    assert cfg.timezone == "Europe/London" and cfg.brief_time == "07:30"
    assert cfg.workdays == [1, 2, 3, 4, 5] and cfg.work_hours == [8, 18]
    assert cfg.slack_sync_minutes == 60 and cfg.health_hours == 6 and cfg.weekly_review is True
    joined = " ".join(problems)
    for key in ("timezone", "brief_time", "workdays", "work_hours", "slack_sync_minutes", "health_hours",
                "weekly_review"):
        assert key in joined


def test_sanitize_keeps_good_values_untouched():
    good = Config(timezone="America/New_York", brief_time="06:45", workdays=[2, 3, 4], work_hours=[9, 17])
    cfg, problems = schedule.sanitize(good)
    assert problems == [] and cfg is good


def test_due_jobs_survive_a_broken_config(ws, at):
    due = schedule.due_jobs(Config(brief_time="soon", timezone="Nowhere/Here"), RunsLedger(ws),
                            at(2026, 10, 20, 9, 0))
    assert "brief" in names(due)


def test_fmt_age():
    assert schedule.fmt_age(timedelta(minutes=45)) == "45 min"
    assert schedule.fmt_age(timedelta(hours=7)) == "7 h"
    assert schedule.fmt_age(timedelta(days=3, hours=2)) == "3 days"
    assert schedule.fmt_age(timedelta(minutes=-5)) == "0 min"


def test_weekly_review_first_run_waits_for_next_monday(at):
    from brainkit.config import Config
    from brainkit.schedule import due_jobs
    cfg = Config()
    tuesday_afternoon = at(2026, 10, 20, 15, 0)  # Mon 19 Oct slot was > 24 h ago
    names = [d.name for d in due_jobs(cfg, [], tuesday_afternoon)]
    assert "weekly_review" not in names and "brief" in names
    monday_morning = at(2026, 10, 19, 9, 0)
    assert "weekly_review" in [d.name for d in due_jobs(cfg, [], monday_morning)]
