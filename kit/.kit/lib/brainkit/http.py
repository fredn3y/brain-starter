"""HTTP through the system `curl`.

Why curl: Apple's Command Line Tools Python may not trust the macOS certificate store, while
`/usr/bin/curl` always does. Secrets travel in a curl config on stdin, never in argv, so they
don't show up in `ps`.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from typing import Dict, Mapping, Optional
from urllib.parse import urlencode

from .runner import Runner


class HttpError(RuntimeError):
    pass


@dataclass
class HttpResponse:
    status: int
    body: str
    headers: Dict[str, str] = field(default_factory=dict)

    def json(self):
        return json.loads(self.body or "null")


def _quote(value: str) -> str:
    if "\n" in value or "\r" in value:
        raise HttpError("newline in curl config value")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _parse_headers(raw: str) -> Dict[str, str]:
    """Headers of the LAST response block (after redirects)."""
    blocks = [b for b in raw.replace("\r\n", "\n").split("\n\n") if b.strip()]
    if not blocks:
        return {}
    out: Dict[str, str] = {}
    for line in blocks[-1].split("\n")[1:]:
        if ":" in line:
            key, value = line.split(":", 1)
            out[key.strip().lower()] = value.strip()
    return out


def request(
    runner: Runner,
    method: str,
    url: str,
    headers: Optional[Mapping[str, str]] = None,
    form: Optional[Mapping[str, str]] = None,
    timeout: int = 30,
) -> HttpResponse:
    """One HTTP request. Raises HttpError only when curl itself fails (DNS, TLS, timeout)."""
    lines = [f"url = {_quote(url)}", f"request = {_quote(method.upper())}",
             f"max-time = {int(timeout)}", "silent", "show-error", "location"]
    for key, value in (headers or {}).items():
        lines.append(f"header = {_quote(f'{key}: {value}')}")
    if form:
        lines.append(f"data = {_quote(urlencode(dict(form)))}")
    fd, header_path = tempfile.mkstemp(prefix="brain-hdr-")
    os.close(fd)
    try:
        result = runner.run(["curl", "--config", "-", "--dump-header", header_path,
                             "--write-out", "\n%{http_code}"],
                            input="\n".join(lines) + "\n", timeout=timeout + 15)
        if not result.ok:
            raise HttpError(f"curl failed ({result.returncode}): {result.stderr.strip()[:200]}")
        body, _, code = result.stdout.rpartition("\n")
        try:
            raw_headers = open(header_path, encoding="utf-8", errors="replace").read()
        except OSError:
            raw_headers = ""
        try:
            status = int(code.strip())
        except ValueError:
            raise HttpError("curl gave no status code") from None
        return HttpResponse(status=status, body=body, headers=_parse_headers(raw_headers))
    finally:
        try:
            os.unlink(header_path)
        except OSError:
            pass


def get_json(runner: Runner, url: str, headers: Optional[Mapping[str, str]] = None, timeout: int = 30):
    resp = request(runner, "GET", url, headers=headers, timeout=timeout)
    if resp.status != 200:
        raise HttpError(f"GET {url}: HTTP {resp.status}")
    return resp.json()


def download(runner: Runner, url: str, dest: str, timeout: int = 600) -> None:
    """Download a (possibly binary) file to `dest`. No secrets: plain argv is fine."""
    result = runner.run(["curl", "-fsSL", "--max-time", str(int(timeout)), "-o", dest, url],
                        timeout=timeout + 15)
    if not result.ok:
        raise HttpError(f"download failed ({result.returncode}): {url}: {result.stderr.strip()[:200]}")
