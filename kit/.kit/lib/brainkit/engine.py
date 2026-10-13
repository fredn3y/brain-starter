"""Headless engine runs: `claude -p "/<skill> …"` with a fixed list of allowed tools.

The command follows the Claude Code docs order: the prompt right after `-p`, the variadic
`--allowedTools` last. `dontAsk` plus `--permission-prompts none` denies anything not allowed
instead of waiting for a person. Paths in Edit rules given on the command line resolve against
the working directory (the workspace), so `Edit(/briefs/**)` means ~/brain/briefs/**; Write is
governed by Edit rules. Outputs stay out of `.claude/` (protected in dontAsk mode).

Raw engine output goes ONLY to logs/engine/<skill>-<YYYY-MM-DD>.log, never to brain.log (which
is shared, redacted, in support bundles). Failure reasons are fixed, content-free strings.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .clock import to_iso, tz
from .jobs.base import JobContext
from .report import redact_secrets

DEFAULT_TOOLS = ["Read", "Glob", "Grep", "Edit(/briefs/**)"]
AVAILABLE_TOOLS = "Read,Glob,Grep,Edit,Write"  # the only tools a scheduled run has
AUTH_HINT = "Claude login expired — open Terminal and run `claude` to log in again"
AUTH_SIGNS = ("failed to authenticate", "not logged in", "login expired", "please run /login",
              "authentication_failed", "invalid api key", "oauth token has expired")
# Set inside a Claude Code session; a run started from chat must not look nested.
DROP_ENV = ("CLAUDECODE",)
ENGINE_LOG_DAYS = 30


@dataclass
class EngineResult:
    ok: bool
    returncode: int
    result_text: str
    cost_usd: Optional[float]
    log_path: Optional[Path]
    reason: str = ""  # short, content-free explanation when not ok


def find_claude(home: Any, path: Optional[str] = None) -> str:
    """`claude` on PATH (the job's PATH when given), else the native installer's ~/.local/bin/claude."""
    found = shutil.which("claude", path=path) if path else shutil.which("claude")
    return found or str(Path(str(home)) / ".local" / "bin" / "claude")


def build_argv(claude: str, skill: str, args_text: str, allowed_tools: Sequence[str], model: str,
               max_turns: int) -> List[str]:
    # `--tools` limits which tools exist at all: no shell, whatever the folder's allow rules say
    # once it is trusted. The run reads untrusted mail and Slack text, so it must not be able to
    # run commands.
    return [claude, "-p", f"/{skill}\n\n{args_text}", "--permission-mode", "dontAsk",
            "--permission-prompts", "none", "--output-format", "json", "--max-turns", str(int(max_turns)),
            "--model", str(model), "--tools", AVAILABLE_TOOLS,
            "--allowedTools"] + [str(tool) for tool in allowed_tools]


def engine_env(ctx: JobContext) -> Dict[str, str]:
    """The job's environment (PATH, HOME, USER, …), plus the stored Claude token if there is one."""
    env = dict(ctx.env) if ctx.env else dict(os.environ)
    for key in DROP_ENV:
        env.pop(key, None)
    token = _oauth_token(ctx)
    if token:
        env["CLAUDE_CODE_OAUTH_TOKEN"] = token
    return env


def _oauth_token(ctx: JobContext) -> Optional[str]:
    try:
        from . import secrets

        return secrets.get(ctx.runner, "claude-oauth-token")
    except Exception:  # no Keychain (not a Mac) or locked: run with Claude's own login
        return None


def parse_output(stdout: str) -> Optional[Dict[str, Any]]:
    """The `--output-format json` result object, tolerating stray lines before it."""
    text = (stdout or "").strip()
    if not text:
        return None
    candidates = [text] + [line.strip() for line in reversed(text.splitlines()) if line.strip().startswith("{")]
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(data, dict):
            return data
    return None


def _cost(data: Optional[Dict[str, Any]]) -> Optional[float]:
    value = (data or {}).get("total_cost_usd")
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _reason(returncode: int, data: Optional[Dict[str, Any]], text: str, stderr: str, raw: str,
            timeout: float) -> str:
    """'' when the run succeeded, else a short explanation that contains none of the output."""
    subtype = str((data or {}).get("subtype") or "")
    if returncode == 0 and data is not None and not data.get("is_error") and not subtype.startswith("error"):
        return ""
    if returncode == 127:
        return "Claude Code isn't installed — run `brain doctor`"
    if returncode == 124 and "timed out" in stderr:
        return f"the AI took longer than {int(timeout // 60)} minutes and was stopped"
    signs = "\n".join([text, stderr, "" if data else raw]).lower()
    if any(sign in signs for sign in AUTH_SIGNS):
        return AUTH_HINT
    if data is None:
        return f"Claude gave no readable answer (exit {returncode})"
    if subtype == "error_max_turns":
        return "the AI ran out of steps before finishing"
    return f"Claude reported an error ({subtype or f'exit {returncode}'})"


def write_log(ctx: JobContext, skill: str, argv: Sequence[str], returncode: int, stdout: str,
              stderr: str) -> Optional[Path]:
    """Append the raw run to logs/engine/<skill>-<date>.log (mode 600). Never raises."""
    try:
        folder = ctx.ws.logs / "engine"
        folder.mkdir(parents=True, exist_ok=True)
        day = ctx.now.astimezone(tz(ctx.config.timezone)).strftime("%Y-%m-%d")
        path = folder / f"{skill}-{day}.log"
        record = (f"===== {to_iso(ctx.now)} exit {returncode}\n--- command\n{' '.join(argv[:2])} …\n"
                  f"--- stdout\n{redact_secrets(stdout or '')}\n--- stderr\n{redact_secrets(stderr or '')}\n")
        with os.fdopen(os.open(str(path), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600), "a",
                       encoding="utf-8") as handle:
            handle.write(record)
        _prune(folder)
        return path
    except OSError:
        return None


def _prune(folder: Path, days: int = ENGINE_LOG_DAYS) -> None:
    cutoff = time.time() - days * 86400
    for old in folder.glob("*.log"):
        try:
            if old.stat().st_mtime < cutoff:
                old.unlink()
        except OSError:
            continue


def run_skill(ctx: JobContext, skill: str, args_text: str, allowed_tools: Sequence[str],
              timeout: float = 900, max_turns: int = 40) -> EngineResult:
    """Run one skill headless in the workspace. Never raises for a failed run."""
    if ctx.config.engine != "claude":
        return EngineResult(False, 2, "", None, None, f"the '{ctx.config.engine}' engine isn't supported yet")
    env = engine_env(ctx)
    claude = find_claude(env.get("HOME") or Path.home(), env.get("PATH"))
    argv = build_argv(claude, skill, args_text, allowed_tools, ctx.config.brief_model, max_turns)
    result = ctx.runner.run(argv, env=env, timeout=timeout, cwd=str(ctx.ws.root))
    log_path = write_log(ctx, skill, argv, result.returncode, result.stdout, result.stderr)
    data = parse_output(result.stdout)
    text = data.get("result") if data and isinstance(data.get("result"), str) else ""
    reason = _reason(result.returncode, data, text, result.stderr or "", result.stdout or "", timeout)
    if reason:
        ctx.log.warning("engine %s: not ok (exit %s): %s", skill, result.returncode, reason)
    else:
        cost = _cost(data)
        ctx.log.info("engine %s: ok%s", skill, f" (${cost:.2f})" if cost is not None else "")
    return EngineResult(not reason, result.returncode, text, _cost(data), log_path, reason)
