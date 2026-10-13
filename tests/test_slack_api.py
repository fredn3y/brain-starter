"""slack_api.py: calls over curl, retries, scope/token errors, pagination, pacing."""

from __future__ import annotations

import json
import re
from urllib.parse import parse_qsl

import pytest

from brainkit import slack_api
from brainkit.runner import FakeRunner, Result

TOKEN = "xoxp-1111-2222-test-token"


def respond(fake: FakeRunner, replies):
    """curl responder: each reply is (status, payload, headers); the last one repeats."""
    queue = list(replies)

    def handler(argv):
        status, payload, headers = queue.pop(0) if len(queue) > 1 else queue[0]
        with open(argv[argv.index("--dump-header") + 1], "w") as handle:
            handle.write(f"HTTP/2 {status}\n" + "".join(f"{k}: {v}\n" for k, v in headers.items()) + "\n")
        return Result(0, f"{json.dumps(payload)}\n{status}")

    fake.on("curl", result=handler)
    return fake


def sent(fake: FakeRunner, index: int = -1):
    config = fake.calls[index]["input"]
    url = re.search(r'^url = "(.*)"$', config, re.M).group(1)
    data = re.search(r'^data = "(.*)"$', config, re.M)
    return url, dict(parse_qsl(data.group(1))) if data else {}, config


def test_call_posts_form_with_token_only_in_stdin(fake):
    respond(fake, [(200, {"ok": True, "messages": []}, {})])
    payload = slack_api.call(fake, "conversations.history", TOKEN,
                             {"channel": "C1", "limit": 200, "inclusive": False, "cursor": None})
    assert payload["ok"] is True
    url, form, config = sent(fake)
    assert url == "https://slack.com/api/conversations.history"
    assert form == {"channel": "C1", "limit": "200", "inclusive": "false"}
    assert f'header = "Authorization: Bearer {TOKEN}"' in config
    assert TOKEN not in " ".join(fake.calls[0]["argv"])


def test_http_429_waits_for_retry_after_capped_at_30s(fake):
    respond(fake, [(429, {}, {"retry-after": "120"}), (200, {"ok": True}, {})])
    waits = []
    assert slack_api.call(fake, "users.info", TOKEN, {"user": "U1"}, sleep=waits.append)["ok"]
    assert waits == [30.0] and len(fake.calls) == 2


def test_ratelimited_error_backs_off_then_gives_up(fake):
    respond(fake, [(200, {"ok": False, "error": "ratelimited"}, {})])
    waits = []
    with pytest.raises(slack_api.SlackError) as info:
        slack_api.call(fake, "conversations.history", TOKEN, sleep=waits.append)
    assert info.value.error == "ratelimited" and len(fake.calls) == slack_api.MAX_ATTEMPTS
    assert len(waits) == slack_api.MAX_ATTEMPTS - 1 and all(0 < w <= 30 for w in waits)


def test_missing_scope_names_the_scope_and_the_fix(fake):
    respond(fake, [(200, {"ok": False, "error": "missing_scope", "needed": "search:read"}, {})])
    with pytest.raises(slack_api.SlackError) as info:
        slack_api.call(fake, "search.messages", TOKEN, {"query": "x"})
    message = str(info.value)
    assert "search:read" in message
    assert "update the Slack app scopes from the kit's manifest and reinstall it" in message


def test_other_errors_never_include_the_token(fake):
    respond(fake, [(200, {"ok": False, "error": "channel_not_found"}, {})])
    with pytest.raises(slack_api.SlackError) as info:
        slack_api.call(fake, "conversations.history", TOKEN, {"channel": "C9"})
    assert str(info.value) == "conversations.history: channel_not_found"
    assert not info.value.token_problem
    revoked =respond(FakeRunner(), [(200, {"ok": False, "error": "token_revoked"}, {})])
    with pytest.raises(slack_api.SlackError) as info:
        slack_api.call(revoked, "auth.test", TOKEN)
    assert info.value.token_problem and TOKEN not in str(info.value)
    assert "brain secret set slack" in str(info.value)


def test_curl_failure_becomes_a_slack_error(fake):
    fake.on("curl", result=Result(6, "", "curl: (6) Could not resolve host: slack.com"))
    with pytest.raises(slack_api.SlackError) as info:
        slack_api.call(fake, "auth.test", TOKEN)
    assert "Could not resolve host" in str(info.value) and TOKEN not in str(info.value)


def test_non_json_answer(fake):
    fake.on("curl", result=lambda argv: Result(0, "<html>maintenance</html>\n503"))
    with pytest.raises(slack_api.SlackError, match="unexpected answer"):
        slack_api.call(fake, "auth.test", TOKEN)


def test_auth_test_returns_identity(fake):
    respond(fake, [(200, {"ok": True, "url": "https://team.slack.com/", "team": "Team", "user": "sam",
                          "team_id": "T1", "user_id": "U1", "extra": 1}, {})])
    assert slack_api.auth_test(fake, TOKEN) == {"ok": True, "user": "sam", "user_id": "U1", "team": "Team",
                                                "team_id": "T1", "url": "https://team.slack.com/"}


def test_auth_test_reports_a_rejected_token_without_raising(fake):
    respond(fake, [(200, {"ok": False, "error": "invalid_auth"}, {})])
    assert slack_api.auth_test(fake, TOKEN) == {"ok": False, "error": "invalid_auth", "user": None,
                                                "user_id": None, "team": None, "team_id": None, "url": None}
    offline = FakeRunner().on("curl", result=Result(6, "", "Could not resolve host"))
    with pytest.raises(slack_api.SlackError):
        slack_api.auth_test(offline, TOKEN)


def test_paginate_follows_the_cursor_and_reports_truncation(fake):
    pages = [(200, {"ok": True, "channels": [{"id": "C1"}], "response_metadata": {"next_cursor": "p2"}}, {}),
             (200, {"ok": True, "channels": [{"id": "C2"}], "response_metadata": {"next_cursor": "p3"}}, {}),
             (200, {"ok": True, "channels": [{"id": "C3"}], "response_metadata": {"next_cursor": ""}}, {})]
    respond(fake, pages)
    items = slack_api.paginate(fake, "users.conversations", TOKEN, {"types": "im"}, "channels", max_pages=5)
    assert [c["id"] for c in items] == ["C1", "C2", "C3"] and items.complete
    assert sent(fake, 1)[1]["cursor"] == "p2" and sent(fake, 2)[1]["cursor"] == "p3"
    capped = respond(FakeRunner(), pages)
    items = slack_api.paginate(capped, "users.conversations", TOKEN, {}, "channels", max_pages=2)
    assert [c["id"] for c in items] == ["C1", "C2"] and not items.complete and len(capped.calls) == 2


def test_client_pauses_between_calls(fake):
    respond(fake, [(200, {"ok": True}, {})])
    waits = []
    client = slack_api.Client(fake, TOKEN, sleep=waits.append, pause=0.7)
    for _ in range(3):
        client.call("auth.test")
    assert waits == [0.7, 0.7]


def test_lookup_names_caches_successes_only(fake):
    def users(argv):
        _, form, _ = sent(fake)
        payload = ({"ok": True, "user": {"id": "U1", "real_name": "Robin Lee", "name": "robin"}}
                   if form["user"] == "U1" else {"ok": False, "error": "user_not_found"})
        with open(argv[argv.index("--dump-header") + 1], "w") as handle:
            handle.write("HTTP/2 200\n\n")
        return Result(0, json.dumps(payload) + "\n200")

    fake.on("curl", result=users)
    cache, failed = {}, set()
    client = slack_api.Client(fake, TOKEN)
    slack_api.lookup_names(client, ["U1", "U2", "U2"], cache, failed)
    assert cache == {"U1": "Robin Lee"} and failed == {"U2"} and len(fake.calls) == 2
