"""Slack Web API client (read-only methods only), over `http.request` and the system curl.

The token is the user's own Slack user token (xoxp-…); it travels in an Authorization header
inside curl's stdin config, never in argv, and never appears in errors or logs. Rate limits:
HTTP 429 waits for Retry-After (capped at 30 s) and a `ratelimited` error backs off, for at most
MAX_ATTEMPTS tries. Nothing here can post, edit, react or mark anything read.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Mapping, Optional

from . import http
from .runner import Runner

SLACK_API = "https://slack.com/api/"
MAX_ATTEMPTS = 5
MAX_WAIT = 30.0
TOKEN_ERRORS = ("invalid_auth", "not_authed", "token_revoked", "token_expired", "account_inactive",
                "not_allowed_token_type")
SCOPE_FIX = "update the Slack app scopes from the kit's manifest and reinstall it"
TOKEN_FIX = "connect Slack again with a new token: brain secret set slack --gui"

Sleep = Callable[[float], None]


class SlackError(RuntimeError):
    """A Slack call failed. `error` is Slack's error code when there is one."""

    def __init__(self, message: str, error: Optional[str] = None):
        super().__init__(message)
        self.error = error

    @property
    def token_problem(self) -> bool:
        return self.error in TOKEN_ERRORS


class Pages(list):
    """Items from every page fetched; `complete` is False when max_pages stopped it early."""

    complete = True


def _form_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _retry_after(headers: Mapping[str, str], attempt: int) -> float:
    try:
        wait = float(headers.get("retry-after", ""))
    except ValueError:
        wait = 2.0 * (attempt + 1)
    return max(0.0, min(wait, MAX_WAIT))


def _scrub(text: str, token: str) -> str:
    return text.replace(token, "<token>") if token else text


def call(runner: Runner, method: str, token: str, params: Optional[Mapping[str, Any]] = None,
         sleep: Sleep = time.sleep) -> Dict[str, Any]:
    """One Web API call (form POST). Returns the payload when `ok`, else raises SlackError."""
    form = {key: _form_value(value) for key, value in (params or {}).items() if value is not None}
    headers = {"Authorization": f"Bearer {token}"}
    for attempt in range(MAX_ATTEMPTS):
        last_try = attempt + 1 == MAX_ATTEMPTS
        try:
            resp = http.request(runner, "POST", SLACK_API + method, headers=headers, form=form)
        except http.HttpError as exc:
            raise SlackError(f"{method}: {_scrub(str(exc), token)}") from None
        if resp.status == 429:
            if last_try:
                break
            sleep(_retry_after(resp.headers, attempt))
            continue
        try:
            payload = resp.json()
        except ValueError:
            payload = None
        if not isinstance(payload, dict):
            raise SlackError(f"{method}: unexpected answer from Slack (HTTP {resp.status})")
        if payload.get("ok"):
            return payload
        error = str(payload.get("error") or f"HTTP {resp.status}")
        if error == "ratelimited":
            if last_try:
                break
            sleep(_retry_after(resp.headers, attempt))
            continue
        if error == "missing_scope":
            needed = str(payload.get("needed") or "a missing permission")
            raise SlackError(f"{method}: the Slack app needs the '{needed}' scope — {SCOPE_FIX}", error)
        if error in TOKEN_ERRORS:
            raise SlackError(f"{method}: {error} — {TOKEN_FIX}", error)
        raise SlackError(f"{method}: {_scrub(error, token)}", error)
    raise SlackError(f"{method}: still rate limited after {MAX_ATTEMPTS} tries", "ratelimited")


class Client:
    """A token plus pacing: waits `pause` seconds between calls to stay under rate limits."""

    def __init__(self, runner: Runner, token: str, sleep: Sleep = time.sleep, pause: float = 0.0):
        self.runner = runner
        self.token = token
        self.sleep = sleep
        self.pause = pause
        self.calls = 0

    def call(self, method: str, params: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
        if self.calls and self.pause > 0:
            self.sleep(self.pause)
        self.calls += 1
        return call(self.runner, method, self.token, params, sleep=self.sleep)

    def paginate(self, method: str, params: Optional[Mapping[str, Any]], key: str,
                 max_pages: int = 20) -> Pages:
        items = Pages()
        query = dict(params or {})
        for _ in range(max(1, int(max_pages))):
            payload = self.call(method, query)
            found = payload.get(key)
            if isinstance(found, list):
                items.extend(found)
            cursor = (payload.get("response_metadata") or {}).get("next_cursor")
            if not cursor:
                return items
            query["cursor"] = cursor
        items.complete = False
        return items


def paginate(runner: Runner, method: str, token: str, params: Optional[Mapping[str, Any]], key: str,
             max_pages: int = 20, sleep: Sleep = time.sleep, pause: float = 0.0) -> Pages:
    """Every `key` item across pages (response_metadata.next_cursor), at most `max_pages` pages."""
    return Client(runner, token, sleep=sleep, pause=pause).paginate(method, params, key, max_pages)


def auth_test(runner: Runner, token: str, sleep: Sleep = time.sleep) -> Dict[str, Any]:
    """Who the token belongs to: {"ok", "user", "user_id", "team", "team_id", "url"}. A token
    Slack rejects gives {"ok": False, "error": <code>}; network problems raise SlackError."""
    keys = ("user", "user_id", "team", "team_id", "url")
    try:
        payload = call(runner, "auth.test", token, sleep=sleep)
    except SlackError as exc:
        if not exc.token_problem:
            raise
        return dict({key: None for key in keys}, ok=False, error=exc.error)
    return dict({key: payload.get(key) for key in keys}, ok=True)


def user_name(user: Mapping[str, Any]) -> str:
    """Best display name from a users.info `user` object."""
    profile = user.get("profile") if isinstance(user.get("profile"), dict) else {}
    for value in (user.get("real_name"), profile.get("real_name"), profile.get("display_name"),
                  user.get("name"), user.get("id")):
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def lookup_names(client: Client, user_ids: List[str], cache: Dict[str, str],
                 failed: Optional[set] = None) -> None:
    """Add names for unknown ids to `cache` (users.info). Ids that fail go in `failed` (if given)
    so one run asks once; they are not cached, so a later run tries again."""
    failed = set() if failed is None else failed
    for user_id in user_ids:
        if not user_id or user_id in cache or user_id in failed:
            continue
        try:
            found = client.call("users.info", {"user": user_id}).get("user") or {}
        except SlackError as exc:
            if exc.token_problem:
                raise
            failed.add(user_id)
            continue
        name = user_name(found)
        if name:
            cache[user_id] = name
        else:
            failed.add(user_id)
