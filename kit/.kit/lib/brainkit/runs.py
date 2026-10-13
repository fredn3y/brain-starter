"""Append-only run history in `ledgers/runs.jsonl` (one JSON object per job run)."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from .clock import parse_iso, to_iso
from .paths import Workspace


class RunsLedger:
    def __init__(self, ws: Workspace):
        self.path = ws.runs_file

    def record(
        self,
        job: str,
        status: str,
        started: datetime,
        finished: datetime,
        summary: str = "",
        detail: Optional[str] = None,
        data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        row = {
            "job": job,
            "status": status,
            "started_at": to_iso(started),
            "finished_at": to_iso(finished),
            "summary": summary,
            "detail": detail,
            "data": data or {},
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        return row

    def rows(self) -> List[Dict[str, Any]]:
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        out = []
        for line in lines:
            try:
                out.append(json.loads(line))
            except ValueError:
                continue  # a torn last line must not break the runner
        return out

    def last(self, job: str, status: Optional[str] = "ok") -> Optional[Dict[str, Any]]:
        for row in reversed(self.rows()):
            if row.get("job") == job and (status is None or row.get("status") == status):
                return row
        return None

    def last_finished(self, job: str, status: Optional[str] = "ok") -> Optional[datetime]:
        row = self.last(job, status)
        return parse_iso(row.get("finished_at")) if row else None

    def recent(self, n: int = 50) -> List[Dict[str, Any]]:
        return self.rows()[-n:]
