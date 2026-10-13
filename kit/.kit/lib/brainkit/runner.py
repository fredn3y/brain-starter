"""One door for every external command, so tests can fake macOS tools.

Production code takes a `Runner`; tests pass a `FakeRunner` with canned results.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Tuple


@dataclass
class Result:
    returncode: int
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class Runner:
    """Runs real commands. Never raises for a non-zero exit; callers check `Result.ok`."""

    def run(
        self,
        argv: Sequence[str],
        input: Optional[str] = None,
        env: Optional[Mapping[str, str]] = None,
        timeout: Optional[float] = None,
        cwd: Optional[str] = None,
    ) -> Result:
        try:
            proc = subprocess.run(
                list(argv),
                input=input,
                env=dict(env) if env is not None else None,
                cwd=cwd,
                timeout=timeout,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            return Result(127, "", f"command not found: {argv[0]}")
        except subprocess.TimeoutExpired:
            return Result(124, "", f"timed out after {timeout}s: {argv[0]}")
        return Result(proc.returncode, proc.stdout or "", proc.stderr or "")


Responder = Callable[[List[str]], Result]


@dataclass
class FakeRunner(Runner):
    """Test double. Match rules by argv prefix; record every call.

    rules: list of (argv_prefix, Result or callable(argv) -> Result). First match wins.
    Unmatched commands return `default` (127 unless set).
    """

    rules: List[Tuple[Tuple[str, ...], object]] = field(default_factory=list)
    default: Result = field(default_factory=lambda: Result(127, "", "no fake rule"))
    calls: List[Dict[str, object]] = field(default_factory=list)

    def on(self, *prefix: str, result: object) -> "FakeRunner":
        self.rules.append((tuple(prefix), result))
        return self

    def run(self, argv, input=None, env=None, timeout=None, cwd=None) -> Result:  # type: ignore[override]
        argv = [str(a) for a in argv]
        self.calls.append({"argv": argv, "input": input, "env": dict(env) if env else None, "cwd": cwd})
        for prefix, result in self.rules:
            if tuple(argv[: len(prefix)]) == prefix:
                return result(argv) if callable(result) else result  # type: ignore[misc]
        return self.default

    def argvs(self) -> List[List[str]]:
        return [c["argv"] for c in self.calls]  # type: ignore[misc]
