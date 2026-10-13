"""Named paths inside a brain workspace (default ~/brain)."""

from __future__ import annotations

import os
from pathlib import Path


class Workspace:
    def __init__(self, root: Path):
        self.root = Path(root).expanduser()

    @classmethod
    def default(cls) -> "Workspace":
        return cls(Path(os.environ.get("BRAIN_HOME", "~/brain")).expanduser())

    # kit area
    @property
    def kit(self) -> Path:
        return self.root / ".kit"

    @property
    def kit_bin(self) -> Path:
        return self.kit / "bin"

    @property
    def version_file(self) -> Path:
        return self.kit / "VERSION"

    @property
    def manifest(self) -> Path:
        return self.kit / "manifest.json"

    @property
    def config_file(self) -> Path:
        return self.kit / "config.json"

    @property
    def tools_file(self) -> Path:
        return self.kit / "tools.json"

    @property
    def state(self) -> Path:
        return self.kit / "state"

    @property
    def snapshots(self) -> Path:
        return self.kit / "snapshots"

    @property
    def lock_file(self) -> Path:
        return self.kit / "run.lock"

    # user-owned / generated areas
    @property
    def tasks(self) -> Path:
        return self.root / "tasks"

    @property
    def briefs(self) -> Path:
        return self.root / "briefs"

    @property
    def packets(self) -> Path:
        return self.briefs / ".packets"

    @property
    def ledgers(self) -> Path:
        return self.root / "ledgers"

    @property
    def runs_file(self) -> Path:
        return self.ledgers / "runs.jsonl"

    @property
    def slack_ledger_dir(self) -> Path:
        return self.ledgers / "slack"

    @property
    def slack_cursor(self) -> Path:
        return self.ledgers / "slack-cursor.json"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def reports(self) -> Path:
        return self.root / "reports"

    @property
    def claude_skills(self) -> Path:
        return self.root / ".claude" / "skills"

    @property
    def agents_skills(self) -> Path:
        return self.root / ".agents" / "skills"

    def ensure_dirs(self) -> None:
        for path in (self.kit, self.state, self.briefs, self.packets, self.ledgers,
                     self.slack_ledger_dir, self.logs, self.reports):
            path.mkdir(parents=True, exist_ok=True)
