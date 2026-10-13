"""slack_sync.py (sync, ledger, cursor, recent), slack_cli.py and jobs/slack_sync.py."""

from __future__ import annotations

import functools
import json
import logging
import re
import time
import uuid
from datetime import datetime, timedelta
from urllib.parse import parse_qsl, quote

import pytest

from brainkit import browser, slack_cli, slack_sync
from brainkit.cli import build_parser
from brainkit.clock import tz
from brainkit.config import Config
from brainkit.jobs import slack_sync as slack_job
from brainkit.jobs.base import JobContext
from brainkit.runner import FakeRunner, Result
from brainkit.runs import RunsLedger
from brainkit.slack_api import SlackError

TOKEN = "xoxp-9999-test-token"


class Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


@pytest.fixture
def log():
    logger = logging.getLogger(f"test-slack-{uuid.uuid4().hex}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.capture = Capture()
    logger.addHandler(logger.capture)
    return logger


def slack_ts(now: datetime, minutes_ago: float, seq: int) -> str:
    return f"{int(now.timestamp() - minutes_ago * 60)}.{seq:06d}"


class World:
    """A fake Slack workspace behind curl. History and replies honour `oldest` like Slack."""

    def __init__(self, now: datetime):
        self.now = now
        self.runner = FakeRunner()
        self.requests = []
        self.errors = {"P1": "not_in_channel"}
        self.users = {"U2": "Bob Stone", "U3": "Cara Diaz", "UME": "Me Myself"}
        self.convs = [
            [{"id": "C1", "name": "general", "is_channel": True},
             {"id": "D1", "is_im": True, "user": "U2"}],
            [{"id": "G1", "is_mpim": True, "name": "mpdm-me--bob--cara-1"},
             {"id": "P1", "name": "secret", "is_private": True},
             {"id": "D2", "is_im": True, "user": "U9", "is_user_deleted": True}],
        ]
        t = functools.partial(slack_ts, now)
        self.parent = {"ts": t(30 * 60, 5), "user": "U3", "text": "Plan for Q4", "reply_count": 2}
        self.parent["thread_ts"] = self.parent["ts"]
        self.history = {
            "C1": [{"ts": t(60, 1), "user": "U2", "text": "Hi <@UME> can you check the numbers?"},
                   {"ts": t(50, 2), "bot_id": "B1", "subtype": "bot_message", "text": "Deploy finished"},
                   {"ts": t(40, 3), "user": "U3", "subtype": "channel_join", "text": "<@U3> has joined"},
                   {"ts": t(30, 4), "user": "U3", "subtype": "file_share", "text": "",
                    "files": [{"id": "F1", "name": "report.pdf", "mimetype": "application/pdf", "size": 9}]},
                   self.parent],
            "D1": [{"ts": t(10, 8), "user": "U2", "text": "Quick question about invoices"}],
            "G1": [{"ts": t(5, 9), "user": "U3", "text": "Lunch?"}],
        }
        self.replies = {("C1", self.parent["ts"]): [
            {"ts": t(25, 6), "user": "U2", "text": "On it", "thread_ts": self.parent["ts"]},
            {"ts": t(20, 7), "user": "UME", "text": "Done", "thread_ts": self.parent["ts"]}]}
        self.parent["latest_reply"] = self.replies[("C1", self.parent["ts"])][-1]["ts"]
        self.history_override = None
        self.runner.on("curl", result=self.respond)

    def count(self, method):
        return sum(1 for m, _ in self.requests if m == method)

    def params(self, method):
        return [p for m, p in self.requests if m == method]

    def respond(self, argv):
        config = self.runner.calls[-1]["input"]
        method = re.search(r'^url = "(.*)"$', config, re.M).group(1).rsplit("/", 1)[-1]
        data = re.search(r'^data = "(.*)"$', config, re.M)
        params = dict(parse_qsl(data.group(1))) if data else {}
        self.requests.append((method, params))
        payload = self.answer(method, params)
        with open(argv[argv.index("--dump-header") + 1], "w") as handle:
            handle.write("HTTP/2 200\n\n")
        return Result(0, json.dumps(payload) + "\n200")

    def answer(self, method, params):
        if method == "auth.test":
            return {"ok": True, "user_id": "UME", "user": "me", "team": "Team", "team_id": "T1",
                    "url": "https://team.slack.com/"}
        if method == "users.conversations":
            page = 1 if params.get("cursor") == "p2" else 0
            meta = {"next_cursor": "p2" if page == 0 else ""}
            return {"ok": True, "channels": self.convs[page], "response_metadata": meta}
        if method == "users.info":
            name = self.users.get(params["user"])
            return {"ok": True, "user": {"id": params["user"], "real_name": name}} if name else \
                {"ok": False, "error": "user_not_found"}
        if method == "search.messages":
            ts = self.history["C1"][0]["ts"]
            return {"ok": True, "messages": {"total": 1, "matches": [
                {"channel": {"id": "C1", "name": "general"}, "user": "U2", "username": "bob", "ts": ts,
                 "text": "the numbers are in", "permalink": "https://team.slack.com/archives/C1/p1"}]}}
        channel = params.get("channel")
        if channel in self.errors:
            return {"ok": False, "error": self.errors[channel]}
        oldest = float(params.get("oldest") or 0)
        if method == "conversations.history":
            if self.history_override:
                return self.history_override(params)
            msgs = [m for m in self.history.get(channel, []) if float(m["ts"]) > oldest]
            return {"ok": True, "messages": sorted(msgs, key=lambda m: -float(m["ts"])), "has_more": False}
        if method == "conversations.replies":
            parent = next(m for m in self.history[channel] if m["ts"] == params["ts"])
            later = [m for m in self.replies.get((channel, params["ts"]), []) if float(m["ts"]) > oldest]
            return {"ok": True, "messages": [parent] + later, "has_more": False}
        return {"ok": False, "error": "unknown_method"}


@pytest.fixture
def now(at):
    return at(2026, 10, 21, 9, 12)


@pytest.fixture
def world(now):
    return World(now)


def ledger_rows(ws):
    return [json.loads(line) for path in slack_sync.month_files(ws) for line in path.read_text().splitlines()]


def by_text(rows):
    return {row["text"]: row for row in rows}


def run_sync(ws, world, log, when=None, **kwargs):
    kwargs.setdefault("pause", 0)
    return slack_sync.sync(ws, world.runner, TOKEN, when or world.now, log, **kwargs)


# ---------------------------------------------------------------- sync


def test_first_sync_writes_human_messages_with_all_fields(ws, world, log, now):
    summary = run_sync(ws, world, log)
    assert summary["conversations"] == 4 and summary["errors"] == 1  # D2 (deleted user) skipped; P1 failed
    assert (summary["messages"], summary["dms"], summary["mentions"], summary["threads"]) == (5, 2, 1, 1)
    rows = ledger_rows(ws)
    assert [p.name for p in slack_sync.month_files(ws)] == ["2026-10.jsonl"]
    texts = by_text(rows)
    assert set(texts) == {"Hi <@UME> can you check the numbers?", "", "On it", "Done",
                          "Quick question about invoices", "Lunch?"}  # no bot, join or 30 h old parent
    first = texts["Hi <@UME> can you check the numbers?"]
    ts = world.history["C1"][0]["ts"]
    assert first == {
        "id": f"C1:{ts}", "channel": "C1", "channel_name": "#general", "kind": "channel", "ts": ts,
        "time": datetime.fromtimestamp(float(ts), tz("Europe/London")).isoformat(timespec="seconds"),
        "user": "U2", "user_name": "Bob Stone", "from_me": False, "mentions_me": True,
        "text": "Hi <@UME> can you check the numbers?", "permalink": f"https://team.slack.com/archives/C1/p{ts.replace('.', '')}",
        "thread_ts": None, "reply_count": 0, "in_thread": False, "files": []}
    done = texts["Done"]
    assert done["from_me"] and done["in_thread"] and done["user_name"] == "Me Myself"
    assert done["permalink"].endswith(f"?thread_ts={world.parent['ts']}&cid=C1")
    assert texts[""]["files"] == [{"name": "report.pdf", "mimetype": "application/pdf"}]
    assert texts["Quick question about invoices"]["channel_name"] == "DM with Bob Stone"
    assert texts["Quick question about invoices"]["kind"] == "dm"
    assert texts["Lunch?"]["channel_name"] == "Group DM (me, bob, cara)" and texts["Lunch?"]["kind"] == "group_dm"
    cursor = json.loads(ws.slack_cursor.read_text())
    assert cursor == {"C1": world.history["C1"][3]["ts"], "D1": world.history["D1"][0]["ts"],
                      "G1": world.history["G1"][0]["ts"]}
    names = json.loads((ws.ledgers / "slack-users.json").read_text())
    assert names["U2"] == "Bob Stone" and names["UME"] == "Me Myself"


def test_request_parameters(ws, world, log, now):
    run_sync(ws, world, log)
    convs = world.params("users.conversations")[0]
    assert convs == {"types": "public_channel,private_channel,mpim,im", "exclude_archived": "true", "limit": "200"}
    history = world.params("conversations.history")[0]
    assert history["channel"] == "C1" and history["limit"] == "200" and history["inclusive"] == "false"
    assert float(history["oldest"]) == pytest.approx(now.timestamp() - 72 * 3600, abs=0.01)
    replies = world.params("conversations.replies")[0]
    assert replies["ts"] == world.parent["ts"] and replies["inclusive"] == "false"


def test_second_sync_adds_nothing_and_skips_known_threads(ws, world, log, now):
    run_sync(ws, world, log)
    before = ledger_rows(ws)
    summary = run_sync(ws, world, log, when=now + timedelta(hours=1))
    assert summary["messages"] == 0 and summary["threads"] == 0
    assert ledger_rows(ws) == before and world.count("conversations.replies") == 1
    assert world.count("users.info") == 3  # names come from the cache now


def test_a_new_reply_to_an_older_thread_is_picked_up(ws, world, log, now):
    run_sync(ws, world, log)
    later = now + timedelta(hours=2)
    reply = {"ts": slack_ts(later, 1, 10), "user": "U2", "text": "One more thing <@UME>",
             "thread_ts": world.parent["ts"]}
    world.replies[("C1", world.parent["ts"])].append(reply)
    world.parent["latest_reply"] = reply["ts"]
    summary = run_sync(ws, world, log, when=later)
    assert summary["messages"] == 1 and summary["mentions"] == 1 and summary["threads"] == 1
    rows = ledger_rows(ws)
    assert len(rows) == len({r["id"] for r in rows}) == 7
    assert by_text(rows)["One more thing <@UME>"]["in_thread"]


def test_thread_budget_defers_the_rest(ws, world, log, now):
    second = {"ts": slack_ts(now, 15, 11), "user": "U3", "text": "Another thread", "reply_count": 1}
    second.update(thread_ts=second["ts"], latest_reply=slack_ts(now, 2, 12))
    world.history["D1"].append(second)
    world.replies[("D1", second["ts"])] = [{"ts": second["latest_reply"], "user": "U2", "text": "Reply",
                                            "thread_ts": second["ts"]}]
    summary = run_sync(ws, world, log, max_threads=1)
    assert summary["threads"] == 1 and summary["threads_deferred"] == 1
    assert world.count("conversations.replies") == 1
    summary = run_sync(ws, world, log, max_threads=5)  # the deferred thread is fetched next time
    assert summary["threads"] == 1 and "Reply" in by_text(ledger_rows(ws))


def test_pause_between_calls(ws, world, log):
    waits = []
    run_sync(ws, world, log, pause=0.7, sleep=waits.append)
    assert waits == [0.7] * (len(world.requests) - 1)


def test_failing_conversation_is_logged_without_message_text(ws, world, log):
    run_sync(ws, world, log)
    text = "\n".join(log.capture.lines)
    assert "P1" in text and "not_in_channel" in text
    for private in ("numbers", "Quick question", "Lunch", "On it", "report.pdf", "Bob Stone"):
        assert private not in text


def test_an_unexpected_error_in_one_conversation_is_skipped(ws, world, log, monkeypatch):
    original = slack_sync._Sync.conversation

    def flaky(self, conv, cursor):
        if conv["id"] == "D1":
            raise ValueError("Quick question about invoices")  # content must not reach the log
        return original(self, conv, cursor)

    monkeypatch.setattr(slack_sync._Sync, "conversation", flaky)
    summary = run_sync(ws, world, log)
    assert summary["errors"] == 2 and summary["messages"] == 4  # P1 and D1 failed; the rest synced
    text = "\n".join(log.capture.lines)
    assert "D1 (ValueError)" in text and "Quick question" not in text


def test_token_problems_stop_the_sync(ws, world, log):
    world.errors["C1"] = "invalid_auth"
    with pytest.raises(SlackError) as info:
        run_sync(ws, world, log)
    assert info.value.token_problem and TOKEN not in str(info.value)
    assert not ws.slack_cursor.exists()


def test_cursor_is_written_only_after_the_ledger_append(ws, world, log, monkeypatch):
    def broken(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(slack_sync, "append_entries", broken)
    with pytest.raises(OSError):
        run_sync(ws, world, log)
    assert not ws.slack_cursor.exists()


def test_backlog_bigger_than_one_sync_is_reported(ws, world, log, now, monkeypatch):
    monkeypatch.setattr(slack_sync, "HISTORY_PAGES", 2)
    counter = iter(range(100, 200))

    def endless(params):
        return {"ok": True, "has_more": True, "response_metadata": {"next_cursor": "more"},
                "messages": [{"ts": slack_ts(now, 1, next(counter)), "user": "U2", "text": "spam"}]}

    world.history_override = endless
    summary = run_sync(ws, world, log)
    assert summary["gaps"] == 3  # C1, D1 and G1 each hit the page cap
    assert any("more new messages than one sync reads" in line for line in log.capture.lines)


def test_append_never_glues_onto_a_torn_line(ws):
    path = ws.slack_ledger_dir / "2026-10.jsonl"
    path.write_text('{"id": "C1:1.000001", "ts": "1.000001"}\n{"id": "C1:2.0000')
    ts = f"{int(datetime(2026, 10, 21, tzinfo=tz('Europe/London')).timestamp())}.000100"
    slack_sync.append_entries(ws, [{"id": f"C1:{ts}", "ts": ts, "text": "x"}], tz("Europe/London"))
    rows = list(slack_sync.read_rows(path))
    assert [r["id"] for r in rows] == ["C1:1.000001", f"C1:{ts}"]


# ---------------------------------------------------------------- recent


def write_rows(ws, month, rows):
    path = ws.slack_ledger_dir / f"{month}.jsonl"
    with path.open("a") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")


def test_recent_filters_and_orders(ws, at):
    base = at(2026, 10, 1, 9, 0).timestamp()

    def row(mins, kind="channel", mention=False, rid=None):
        ts = f"{int(base + mins * 60)}.000100"
        return {"id": rid or f"X:{ts}", "ts": ts, "kind": kind, "mentions_me": mention, "text": str(mins)}

    write_rows(ws, "2026-09", [row(-60 * 24 * 3, rid="old")])
    write_rows(ws, "2026-10", [row(30), row(10, "dm"), row(20, mention=True), row(10, "dm")])  # a duplicate
    since = at(2026, 10, 1, 9, 5)
    assert [r["text"] for r in slack_sync.recent(ws, since)] == ["10", "20", "30"]
    assert [r["text"] for r in slack_sync.recent(ws, since, kinds=slack_sync.DM_KINDS)] == ["10"]
    assert [r["text"] for r in slack_sync.recent(ws, since, mentions_only=True)] == ["20"]
    assert [r["text"] for r in slack_sync.recent(ws, since, limit=2)] == ["20", "30"]
    assert [r["id"] for r in slack_sync.recent(ws, at(2026, 9, 1))][0] == "old"


def test_readable_text():
    names = {"U1": "Robin Lee"}
    text = "<@U1> see <#C9|general> and <https://example.com|the doc> &amp; <!here> <!subteam^S1|@ops> <@U7|sam>"
    assert slack_sync.readable(text, names) == "@Robin Lee see #general and the doc & @here @ops @sam"


# ---------------------------------------------------------------- CLI and job


def test_cli_recent_prints_short_lines(ws, capsys):
    ts_value = time.time() - 600
    ts = f"{int(ts_value)}.000100"
    month = datetime.fromtimestamp(ts_value, tz("Europe/London")).strftime("%Y-%m")
    write_rows(ws, month, [{"id": f"C1:{ts}", "ts": ts, "kind": "channel", "channel": "C1", "channel_name": "#general",
                            "user": "U2", "user_name": "Bob Stone", "mentions_me": True, "from_me": False,
                            "text": "Hi <@UME> can you check\nthe numbers?",
                            "permalink": f"https://team.slack.com/archives/C1/p{ts.replace('.', '')}"}])
    (ws.ledgers / "slack-users.json").write_text(json.dumps({"UME": "Me Myself"}))
    args = build_parser().parse_args(["--workspace", str(ws.root), "slack", "recent", "--hours", "2"])
    assert args.func(args) == 0
    line = capsys.readouterr().out.strip()
    assert re.match(r"^\w{3} \d{2}:\d{2} · #general · Bob Stone: Hi @Me Myself can you check the numbers\? "
                    r"<https://team\.slack\.com/archives/C1/p\d+>$", line), line
    args = build_parser().parse_args(["--workspace", str(ws.root), "slack", "recent", "--json", "--dms"])
    assert args.func(args) == 0 and json.loads(capsys.readouterr().out) == []


def test_cli_create_app_opens_the_prefilled_page(ws, monkeypatch, capsys):
    manifest = "display_information:\n  name: Brain Slack Reader\noauth_config:\n  scopes:\n    user:\n      - im:history\n"
    (ws.kit / "slack-manifest.yaml").write_text(manifest)
    fake = FakeRunner().on("open", result=Result(0))
    monkeypatch.setattr(slack_cli, "_runner", lambda: fake)
    monkeypatch.setattr(slack_cli, "_platform", lambda: "darwin")
    monkeypatch.setattr(browser, "chrome_installed", lambda home=None, exists=None: False)
    args = build_parser().parse_args(["--workspace", str(ws.root), "slack", "create-app"])
    assert args.func(args) == 0
    url = "https://api.slack.com/apps?new_app=1&manifest_yaml=" + quote(manifest, safe="")
    assert url in capsys.readouterr().out and fake.argvs() == [["open", url]]
    monkeypatch.setattr(browser, "chrome_installed", lambda home=None, exists=None: True)
    assert args.func(args) == 0
    assert fake.argvs()[-1] == ["open", "-a", "Google Chrome", url]
    (ws.kit / "slack-manifest.yaml").unlink()
    assert args.func(args) == 1
    assert "manifest is missing" in capsys.readouterr().err


@pytest.fixture
def cli(ws, world, monkeypatch, capsys):
    monkeypatch.setattr(slack_cli, "_runner", lambda: world.runner)
    monkeypatch.setattr(slack_cli, "_token", lambda runner: TOKEN)
    monkeypatch.setattr(slack_cli, "sync", functools.partial(slack_sync.sync, pause=0))

    def run(*argv):
        args = build_parser().parse_args(["--workspace", str(ws.root), "slack"] + list(argv))
        code = args.func(args)
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    return run


def test_cli_whoami_sync_search_thread_channels(cli, world, ws):
    code, out, _ = cli("whoami")
    assert code == 0 and out.strip() == "Connected to Slack as me (UME) in Team — https://team.slack.com/"
    code, out, _ = cli("sync")
    assert code == 0 and re.match(r"^\d+ new messages \(2 DMs, 1 mention\); 1 conversation failed$", out.strip())
    assert ledger_rows(ws)
    code, out, _ = cli("search", "numbers", "--count", "500")
    assert code == 0 and "#general · bob: the numbers are in <https://team.slack.com/archives/C1/p1>" in out
    assert world.params("search.messages")[-1] == {"query": "numbers", "count": "100", "sort": "timestamp",
                                                   "sort_dir": "desc"}
    code, out, _ = cli("thread", "C1", world.parent["ts"])
    lines = out.strip().splitlines()
    assert code == 0 and len(lines) == 3 and "Cara Diaz: Plan for Q4" in lines[0]
    assert lines[2].endswith(f"?thread_ts={world.parent['ts']}&cid=C1>") and "Me Myself: Done" in lines[2]
    code, out, _ = cli("channels")
    assert code == 0 and out.splitlines() == [
        "channel   #general  (C1)", "private   #secret  (P1)", "group_dm  Group DM (me, bob, cara)  (G1)",
        "dm        DM with Bob Stone  (D1)", "dm        DM with U9  (D2)"]
    assert TOKEN not in out


def test_cli_whoami_with_a_rejected_token(cli, world):
    world.runner.rules.insert(0, (("curl",), lambda argv: _rejected(argv)))
    code, _, err = cli("whoami")
    assert code == 1 and "Slack rejected the saved token (invalid_auth)" in err and TOKEN not in err


def _rejected(argv):
    with open(argv[argv.index("--dump-header") + 1], "w") as handle:
        handle.write("HTTP/2 200\n\n")
    return Result(0, json.dumps({"ok": False, "error": "invalid_auth"}) + "\n200")


def job_ctx(ws, runner, now, log):
    return JobContext(ws=ws, config=Config(), runner=runner, now=now, runs=RunsLedger(ws), log=log,
                      notify=lambda title, message: None, env={"PATH": "/usr/bin", "HOME": "/tmp", "USER": "tester"})


def test_job_skips_without_a_token(ws, log, now):
    runner = FakeRunner().on("security", "find-generic-password", "-s", "brain.slack", result=Result(44))
    result = slack_job.run(job_ctx(ws, runner, now, log))
    assert (result.status, result.summary) == ("skipped", "Slack not connected yet")


def test_job_syncs_and_summarises(ws, world, log, now, monkeypatch):
    monkeypatch.setattr(slack_sync, "sync", functools.partial(slack_sync.sync, pause=0))
    world.runner.rules.insert(0, (("security", "find-generic-password", "-s", "brain.slack"), Result(0, TOKEN + "\n")))
    result = slack_job.run(job_ctx(ws, world.runner, now, log))
    assert result.status == "ok"
    assert result.summary == "5 new messages (2 DMs, 1 mention); 1 conversation failed"
    assert result.data == {"conversations": 4, "messages": 5, "dms": 2, "mentions": 1, "errors": 1}


def test_job_reports_token_problems(ws, world, log, now, monkeypatch):
    monkeypatch.setattr(slack_sync, "sync", functools.partial(slack_sync.sync, pause=0))
    world.runner.rules.insert(0, (("security", "find-generic-password", "-s", "brain.slack"), Result(0, TOKEN + "\n")))
    world.errors["C1"] = "token_revoked"
    result = slack_job.run(job_ctx(ws, world.runner, now, log))
    assert result.status == "error" and "token_revoked" in result.detail and TOKEN not in result.detail


def test_summary_text_wording():
    assert slack_sync.summary_text({"messages": 12, "dms": 3, "mentions": 1}) == "12 new messages (3 DMs, 1 mention)"
    assert slack_sync.summary_text({"messages": 1, "dms": 0, "mentions": 2}) == "1 new message (0 DMs, 2 mentions)"
