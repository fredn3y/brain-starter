"""Google Workspace through the `gws` CLI: JSON reads, a send guard, auth status, Gmail drafts.

The brain reads mail, calendar and Drive and makes drafts; it never sends. `is_blocked` refuses
the same commands as the `.kit/bin/gws` shim (keep the two in step). The `brain google …` and
`brain draft email` commands live in google_cli.py.

Errors name the command's shape only ("gmail +read"), never message content or tokens.
"""

from __future__ import annotations

import base64
import json
import re
from email import policy as email_policy
from email.message import EmailMessage
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .report import redact_secrets
from .runner import Result, Runner

BLOCKED_HELPERS = ("+send", "+reply", "+reply-all", "+forward")
# Options that take a value: the value is skipped when finding the command words, so that
# `--query send` isn't mistaken for a send and `users --format json messages send` still is.
VALUE_FLAGS = frozenset({
    "--params", "--json", "--format", "--output", "-o", "--sanitize", "--upload",
    "--upload-content-type", "--page-limit", "--page-delay", "--api-version", "--query", "--max",
    "--id", "--message-id", "--to", "--cc", "--bcc", "--subject", "--body", "--calendar",
    "--timezone", "--days", "--scopes", "-s", "--services",
})
BLOCKED_MESSAGE = ("Blocked by your brain's safety rules: sending email from the brain is turned off. "
                   "Make a draft instead: brain draft email --to … --subject … --body-file …")

SCOPE_BASE = "https://www.googleapis.com/auth/"
GMAIL_READONLY = SCOPE_BASE + "gmail.readonly"
GMAIL_COMPOSE = SCOPE_BASE + "gmail.compose"
CALENDAR_READONLY = SCOPE_BASE + "calendar.readonly"
DRIVE_READONLY = SCOPE_BASE + "drive.readonly"
REQUIRED_SCOPES = (GMAIL_READONLY, GMAIL_COMPOSE, CALENDAR_READONLY, DRIVE_READONLY)
# Broader grants that include a needed scope (an older sign-in may hold these).
BROADER_SCOPES = {
    GMAIL_READONLY: (SCOPE_BASE + "gmail.modify", "https://mail.google.com/"),
    GMAIL_COMPOSE: (SCOPE_BASE + "gmail.modify", "https://mail.google.com/"),
    CALENDAR_READONLY: (SCOPE_BASE + "calendar",),
    DRIVE_READONLY: (SCOPE_BASE + "drive",),
}
SCOPE_LABELS = {GMAIL_READONLY: "read Gmail", GMAIL_COMPOSE: "make Gmail drafts",
                CALENDAR_READONLY: "read Calendar", DRIVE_READONLY: "read Drive"}

RECONNECT = "run `brain google connect`"
NOT_INSTALLED = "gws isn't installed — run `brain tools install gws`"
EXCERPT = 200
NOISE_PREFIXES = ("Using keyring backend",)  # gws prints this on stderr for every command
AUTH_WORDS = ("invalid_grant", "auth login", "no credentials", "not authenticated", "expired",
              "authentication", "unauthorized")


class GwsError(RuntimeError):
    """A gws command failed. Messages never contain message content or tokens."""


class GwsBlocked(GwsError):
    """The command would send email or export Google credentials."""


# ---------------------------------------------------------------- running gws


def command_words(args: Sequence[str]) -> List[str]:
    """The non-option words of a gws command line (option values skipped)."""
    words: List[str] = []
    skip = False
    for arg in (str(a) for a in args):
        if skip:
            skip = False
            continue
        if arg.startswith("-"):
            skip = "=" not in arg and arg in VALUE_FLAGS
            continue
        words.append(arg)
    return words


def is_blocked(args: Sequence[str]) -> bool:
    """True for anything that could send mail or export credentials.

    - a send/reply/forward helper anywhere after `gmail` (even as an option value: conservative);
    - the word `send` (or `….send`) as a command word after `gmail`, wherever options sit;
    - `auth … export`.
    Keep in step with `.kit/bin/gws` (tests share one case table).
    """
    items = [str(arg) for arg in args]
    if "gmail" in items and any(arg in BLOCKED_HELPERS for arg in items[items.index("gmail") + 1:]):
        return True
    words = command_words(items)
    if "gmail" in words:
        after = words[words.index("gmail") + 1:]
        if any(word == "send" or word.endswith(".send") for word in after):
            return True
    if "auth" in words and "export" in words[words.index("auth") + 1:]:
        return True
    return False


def describe(args: Sequence[str]) -> str:
    """The command's shape for messages: its leading words up to the first flag."""
    words: List[str] = []
    for arg in args:
        text = str(arg)
        if text.startswith("-") or len(words) == 4:
            break
        words.append(text)
    return " ".join(words) or "gws"


def _loads(text: Optional[str]) -> Any:
    """JSON from gws output, tolerating log lines before it. None when there's none."""
    text = (text or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        pass
    decoder = json.JSONDecoder()
    for match in re.finditer(r"^[\[{]", text, re.M):
        try:
            return decoder.raw_decode(text[match.start():])[0]
        except ValueError:
            continue
    return None


def _short(text: str) -> str:
    flat = re.sub(r"\s+", " ", redact_secrets(text)).strip()
    return flat if len(flat) <= EXCERPT else flat[: EXCERPT - 1] + "…"


def _failure(args: Sequence[str], result: Result) -> GwsError:
    """A short, safe error for a failed run: gws's JSON error message or one stderr line."""
    stderr = result.stderr or ""
    if "Blocked by your brain's safety rules" in stderr:
        return GwsBlocked(BLOCKED_MESSAGE)
    if result.returncode == 127:
        return GwsError(NOT_INSTALLED)
    if result.returncode == 124 and "timed out" in stderr:
        return GwsError(f"gws {describe(args)} timed out")
    reason, auth = "", False
    data = _loads(result.stdout)
    error = data.get("error") if isinstance(data, dict) else None
    if isinstance(error, dict):
        reason = str(error.get("message") or error.get("reason") or "")
        auth = error.get("code") == 401 or "auth" in str(error.get("reason") or "").lower()
    if not reason:
        lines = [line.strip() for line in stderr.splitlines()
                 if line.strip() and not line.strip().startswith(NOISE_PREFIXES)]
        reason = lines[0] if lines else f"exit {result.returncode}"
    auth = auth or any(word in reason.lower() for word in AUTH_WORDS)
    message = f"gws {describe(args)} failed: {_short(reason)}"
    return GwsError(message + (f" — {RECONNECT}" if auth else ""))


def run_json(runner: Runner, args: Sequence[str], env: Optional[Mapping[str, str]] = None,
             timeout: float = 120) -> Any:
    """Run `gws <args>` and return its parsed JSON (None for empty output)."""
    items = [str(arg) for arg in args]
    if is_blocked(items):
        raise GwsBlocked(BLOCKED_MESSAGE)
    result = runner.run(["gws"] + items, env=dict(env) if env else None, timeout=timeout)
    if not result.ok:
        raise _failure(items, result)
    if not (result.stdout or "").strip():
        return None
    data = _loads(result.stdout)
    if data is None:
        raise GwsError(f"gws {describe(items)} gave an answer that isn't JSON")
    return data


# ---------------------------------------------------------------- auth status


def auth_status(runner: Runner, env: Optional[Mapping[str, str]] = None) -> Dict[str, Any]:
    """`gws auth status` as a dict (user, token_valid, has_refresh_token, scopes, …)."""
    result = runner.run(["gws", "auth", "status"], env=dict(env) if env else None, timeout=60)
    data = _loads(result.stdout)
    if isinstance(data, dict) and "error" not in data:
        return data
    raise _failure(["auth", "status"], result)


def granted_scopes(status: Mapping[str, Any]) -> List[str]:
    raw = status.get("scopes") if isinstance(status, Mapping) else None
    if isinstance(raw, str):
        raw = re.split(r"[\s,]+", raw)
    return [scope.strip() for scope in raw or [] if isinstance(scope, str) and scope.strip()]


def has_scope(status: Mapping[str, Any], scope: str) -> bool:
    granted = set(granted_scopes(status))
    return scope in granted or any(broader in granted for broader in BROADER_SCOPES.get(scope, ()))


def missing_scopes(status: Mapping[str, Any]) -> List[str]:
    return [scope for scope in REQUIRED_SCOPES if not has_scope(status, scope)]


# ---------------------------------------------------------------- drafts


def build_raw_message(to: str, subject: str, body: str, cc: Optional[str] = None,
                      in_reply_to: Optional[str] = None, references: Optional[str] = None,
                      from_addr: Optional[str] = None) -> str:
    """An RFC 2822 message (UTF-8) as the base64url string the Gmail API's `raw` field takes."""
    for label, value in (("To", to), ("Cc", cc), ("Subject", subject), ("From", from_addr),
                         ("In-Reply-To", in_reply_to), ("References", references)):
        if value and ("\n" in value or "\r" in value):
            raise ValueError(f"{label} can't contain line breaks")
    message = EmailMessage()
    message["To"] = to
    if cc:
        message["Cc"] = cc
    if from_addr:
        message["From"] = from_addr
    message["Subject"] = subject
    if in_reply_to:
        message["In-Reply-To"] = in_reply_to
    if references:
        message["References"] = references
    message.set_content(body or "", charset="utf-8")
    return base64.urlsafe_b64encode(message.as_bytes(policy=email_policy.SMTP)).decode("ascii")


def reply_subject(subject: str) -> str:
    text = (subject or "").strip()
    if text.lower().startswith("re:"):
        return text
    return f"Re: {text}" if text else "Re:"


def _headers(message: Mapping[str, Any]) -> Dict[str, str]:
    payload = message.get("payload") if isinstance(message.get("payload"), dict) else {}
    out: Dict[str, str] = {}
    for header in payload.get("headers") or []:
        if isinstance(header, dict) and header.get("name"):
            out[str(header["name"]).lower()] = str(header.get("value") or "")
    return out


def create_draft(runner: Runner, to: str, subject: str, body: str, cc: Optional[str] = None,
                 reply_to_message_id: Optional[str] = None,
                 env: Optional[Mapping[str, str]] = None) -> Dict[str, Any]:
    """Save a Gmail draft (threaded under `reply_to_message_id` when given). {"id", "threadId"}."""
    thread_id = in_reply_to = references = None
    if reply_to_message_id:
        params = {"userId": "me", "id": reply_to_message_id, "format": "metadata",
                  "metadataHeaders": ["Message-ID", "Subject", "From", "References"]}
        original = run_json(runner, ["gmail", "users", "messages", "get", "--params", json.dumps(params)],
                            env=env)
        if not isinstance(original, dict):
            raise GwsError("couldn't read the email to reply to")
        headers = _headers(original)
        thread_id = original.get("threadId")
        message_id = headers.get("message-id", "").strip()
        if message_id:
            in_reply_to = message_id
            earlier = headers.get("references", "").split()
            references = " ".join(earlier + ([] if message_id in earlier else [message_id]))
        subject = reply_subject(subject or headers.get("subject", ""))
    raw = build_raw_message(to, subject or "", body, cc=cc, in_reply_to=in_reply_to, references=references)
    message: Dict[str, Any] = {"raw": raw}
    if thread_id:
        message["threadId"] = thread_id
    result = run_json(runner, ["gmail", "users", "drafts", "create", "--params", json.dumps({"userId": "me"}),
                               "--json", json.dumps({"message": message})], env=env)
    if not isinstance(result, dict) or not result.get("id"):
        raise GwsError("Gmail didn't confirm the draft")
    saved = result.get("message") if isinstance(result.get("message"), dict) else {}
    return {"id": result["id"], "threadId": saved.get("threadId") or thread_id}
