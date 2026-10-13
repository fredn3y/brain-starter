from __future__ import annotations

import pytest

from brainkit import http
from brainkit.runner import FakeRunner, Result


def curl_responder(body: str, code: int, headers: str = "HTTP/2 200\ncontent-type: application/json\n\n"):
    def respond(argv):
        path = argv[argv.index("--dump-header") + 1]
        with open(path, "w") as handle:
            handle.write(headers)
        return Result(0, f"{body}\n{code}")
    return respond


def test_request_keeps_secrets_out_of_argv():
    fake = FakeRunner().on("curl", result=curl_responder('{"ok": true}', 200))
    resp = http.request(fake, "POST", "https://slack.com/api/auth.test",
                        headers={"Authorization": "Bearer xoxp-secret"}, form={"a": "b"})
    assert resp.status == 200 and resp.json() == {"ok": True}
    call = fake.calls[0]
    assert "xoxp-secret" not in " ".join(call["argv"])
    assert 'header = "Authorization: Bearer xoxp-secret"' in call["input"]
    assert 'data = "a=b"' in call["input"]


def test_headers_from_last_block_after_redirect():
    raw = "HTTP/1.1 302 Found\nlocation: x\n\nHTTP/2 429\nretry-after: 7\n\n"
    fake = FakeRunner().on("curl", result=curl_responder("", 429, raw))
    resp = http.request(fake, "GET", "https://example.com")
    assert resp.status == 429 and resp.headers["retry-after"] == "7"


def test_curl_failure_raises():
    fake = FakeRunner().on("curl", result=Result(6, "", "Could not resolve host"))
    with pytest.raises(http.HttpError):
        http.request(fake, "GET", "https://nowhere.invalid")


def test_newline_in_header_rejected():
    with pytest.raises(http.HttpError):
        http.request(FakeRunner(), "GET", "https://x", headers={"X": "a\nb"})
