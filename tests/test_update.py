from __future__ import annotations

import io
import json
import logging
import os
import shutil
import sys
from datetime import datetime

import pytest

from brainkit import apply as apply_mod
from brainkit import http
from brainkit import manifest as manifest_mod
from brainkit import snapshot as snapshot_mod
from brainkit.apply import apply_release, rebuild_links
from brainkit.clock import tz
from brainkit.config import Config
from brainkit.jobs import update_check
from brainkit.jobs.base import JobContext
from brainkit.lock import try_lock
from brainkit.runner import Result, Runner
from brainkit.runs import RunsLedger
from brainkit.update import (BUSY, ReleaseError, check_latest, parse_version, release_from_api,
                             rollback, run_update)
from release_helpers import (REPO, FakeNet, build, extract_release, github_release, make_source,
                             run_apply_in_process, tree, write)

LOG = logging.getLogger("test-update")
API = "https://api.github.com/repos/fredn3y/brain-starter/releases/latest"

V1 = dict(
    files={"AGENTS.md": "rules v1\n", "docs/guide.md": "guide v1\n", "docs/old.md": "old\n",
           ".claude/skills/brief/SKILL.md": "brief v1\n"},
    templates={"me.md": "# Me\n", ".kit/config.json": "{}\n", "tasks/active/.keep": ""},
)
V2 = dict(
    files={"AGENTS.md": "rules v2\n", "docs/guide.md": "guide v2\n", "docs/new.md": "new\n",
           ".claude/skills/brief/SKILL.md": "brief v2\n", ".claude/skills/weekly/SKILL.md": "weekly\n"},
    templates=dict(V1["templates"], **{"voice.md": "# Voice\n"}),
)
BAD_MIGRATION = (
    "from pathlib import Path\n\n"
    "def migrate(workspace):\n"
    "    (Path(workspace) / 'me.md').write_text('clobbered by a broken migration\\n')\n"
    "    raise RuntimeError('migration exploded')\n"
)
HERS = {"me.md": "her answers\n", "tasks/active/plan.md": "# Plan\n",
        "docs/guide.md": "guide edited by her\n", ".kit/config.json": '{"brief_time": "08:00"}\n',
        ".claude/skills/my-notes/SKILL.md": "her skill\n"}


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    path = tmp_path / "home"
    path.mkdir()
    monkeypatch.setenv("HOME", str(path))
    monkeypatch.delenv("BRAIN_HOME", raising=False)
    return path


@pytest.fixture
def dists(tmp_path):
    return {
        "0.1.0": build(make_source(tmp_path / "src1", "0.1.0", **V1), tmp_path / "dist1"),
        "0.2.0": build(make_source(tmp_path / "src2", "0.2.0", **V2), tmp_path / "dist2"),
        "bad": build(make_source(tmp_path / "src3", "0.2.0", migrations={"0002_break.py": BAD_MIGRATION},
                                 **V2), tmp_path / "dist3"),
    }


@pytest.fixture
def installed(tmp_path, ws, home, dists):
    release = extract_release(dists["0.1.0"]["tarball"], tmp_path / "x1")
    apply_release(release, ws, LOG, mode="install", home=home)
    for rel, text in HERS.items():
        write(ws.root / rel, text)
    write(ws.root / "logs" / "brain.log", "a log line\n")
    return ws


class Harness:
    """Wires FakeRunner: fake GitHub downloads, canned doctor output, in-process `_apply`."""

    def __init__(self, fake, before=None, after=None, apply=run_apply_in_process):
        self.fake = fake
        self.net = FakeNet(fake)
        self.before = [{"name": "python", "status": "ok"}] if before is None else before
        self.after = self.before if after is None else after
        self.apply = apply
        self.doctor_runs = 0
        self.apply_calls = []
        self.apply_results = []
        fake.on(sys.executable, result=self.python)

    def python(self, argv):
        if "doctor" in argv:
            self.doctor_runs += 1
            data = self.before if self.doctor_runs == 1 else self.after
            return Result(1, data if isinstance(data, str) else json.dumps(data, indent=2))
        if "_apply" in argv:
            self.apply_calls.append(argv)
            self.apply_results.append(self.apply(argv))
            return self.apply_results[-1]
        return Result(2, "", f"unexpected: {argv}")


def release(version="0.2.0"):
    return release_from_api(github_release(version))


def her_files(ws):
    return {rel: (ws.root / rel).read_bytes() for rel in HERS}


def update(ws, fake, home, **kwargs):
    out = io.StringIO()
    kwargs.setdefault("yes", True)
    code = run_update(ws, Config(), fake, LOG, home=home, out=out, **kwargs)
    return code, out.getvalue()


# --- versions and GitHub -------------------------------------------------------------------

def test_parse_version():
    assert parse_version("v0.3.1") == (0, 3, 1)
    assert parse_version("0.3") == (0, 3, 0)
    assert parse_version("V2") == (2, 0, 0)
    assert parse_version("1.2.3-rc1") == (1, 2, 3)
    assert parse_version("0.10.0") > parse_version("0.9.9")
    with pytest.raises(ValueError):
        parse_version("latest")


def test_check_latest_reads_the_release_and_sends_github_headers(fake):
    net = FakeNet(fake)
    net.api[API] = (200, github_release("0.3.1", notes="Fixes"))
    found = check_latest(fake, "fredn3y/brain-starter")
    assert (found.version, found.tag, found.notes) == ("0.3.1", "v0.3.1", "Fixes")
    assert found.tarball_url.endswith("/v0.3.1/brain-starter-v0.3.1.tar.gz")
    assert found.sums_url.endswith("/v0.3.1/SHA256SUMS")
    assert found.html_url.endswith("/tag/v0.3.1")
    config = fake.calls[0]["input"]
    assert 'header = "Accept: application/vnd.github+json"' in config
    assert 'header = "User-Agent: brain-starter"' in config


def test_check_latest_without_releases_is_none(fake):
    FakeNet(fake)  # unknown URL -> 404
    assert check_latest(fake, "fredn3y/brain-starter") is None


def test_check_latest_errors(fake):
    net = FakeNet(fake)
    net.api[API] = (500, {"message": "oops"})
    with pytest.raises(http.HttpError):
        check_latest(fake, "fredn3y/brain-starter")
    no_assets = dict(github_release("0.3.1"), assets=[])
    net.api[API] = (200, no_assets)
    with pytest.raises(ReleaseError):
        check_latest(fake, "fredn3y/brain-starter")
    plain_http = github_release("0.3.1", base="http://example.invalid")
    net.api[API] = (200, plain_http)
    with pytest.raises(ReleaseError):
        check_latest(fake, "fredn3y/brain-starter")
    with pytest.raises(ReleaseError):
        check_latest(fake, "not a repo; rm")


# --- update --------------------------------------------------------------------------------

def test_happy_path(installed, dists, fake, home):
    ws = installed
    harness = Harness(fake)
    harness.net.serve_release("0.2.0", dists["0.2.0"])
    before_hers = her_files(ws)

    code, text = update(ws, fake, home, release=release())

    assert code == 0, text
    assert ws.version_file.read_text().strip() == "0.2.0"
    assert manifest_mod.load(ws.manifest)["version"] == "0.2.0"
    assert (ws.root / "AGENTS.md").read_text() == "rules v2\n"
    assert (ws.root / "docs/new.md").read_text() == "new\n"
    assert not (ws.root / "docs/old.md").exists()
    assert (ws.root / "docs/guide.md.new").read_text() == "guide v2\n"
    assert (ws.root / "voice.md").read_text() == "# Voice\n"
    assert os.readlink(str(ws.agents_skills / "weekly")) == "../../.claude/skills/weekly"
    assert os.readlink(str(ws.agents_skills / "my-notes")) == "../../.claude/skills/my-notes"
    assert her_files(ws) == before_hers
    assert "from v0.1.0 to v0.2.0" in text and "docs/guide.md.new" in text
    assert harness.doctor_runs == 2 and len(harness.apply_calls) == 1
    call = harness.apply_calls[0]
    assert call[2:4] == ["_apply", "--release-dir"]
    assert call[-5:] == ["--workspace", str(ws.root), "--mode", "update", "--json"]
    assert len(snapshot_mod.list_snapshots(ws)) == 1
    assert snapshot_mod.list_snapshots(ws)[0].name.endswith("-v0.1.0-to-v0.2.0.tgz")
    assert list((ws.kit / "cache").iterdir()) == []  # download area cleaned up
    assert all(c["env"]["HOME"] == str(home) for c in fake.calls if c["argv"][0] == sys.executable)


def test_update_finds_the_latest_release_on_github(installed, dists, fake, home):
    harness = Harness(fake)
    harness.net.api[API] = (200, github_release("0.2.0"))
    harness.net.serve_release("0.2.0", dists["0.2.0"])
    code, text = update(installed, fake, home)
    assert code == 0, text
    assert installed.version_file.read_text().strip() == "0.2.0"


def test_an_interrupted_update_is_offered_again_and_finishes_cleanly(tmp_path, installed, dists,
                                                                     fake, home):
    ws = installed
    v2 = extract_release(dists["0.2.0"]["tarball"], tmp_path / "x2")
    for rel in (".kit/VERSION", "AGENTS.md", "docs/new.md"):  # half-written, then the power went
        write(ws.root / rel, (v2 / "kit" / rel).read_bytes())
    harness = Harness(fake)
    harness.net.serve_release("0.2.0", dists["0.2.0"])
    code, text = update(ws, fake, home, release=release())
    assert code == 0, text
    assert "from v0.1.0 to v0.2.0" in text
    report = json.loads(harness.apply_results[0].stdout)["report"]
    assert {".kit/VERSION", "AGENTS.md", "docs/new.md"} <= set(report["unchanged"])
    assert report["kept_modified"] == ["docs/guide.md"]  # only her real edit
    assert manifest_mod.load(ws.manifest)["version"] == "0.2.0"


def test_version_is_written_last(tmp_path, ws, home, monkeypatch):
    from brainkit import apply as apply_mod

    written = []
    real = apply_mod._write_copy

    def spy(src, dst, mode):
        written.append(dst.relative_to(ws.root).as_posix())
        real(src, dst, mode)

    monkeypatch.setattr(apply_mod, "_write_copy", spy)
    dist = build(make_source(tmp_path / "s", "0.1.0", **V1), tmp_path / "d")
    apply_release(extract_release(dist["tarball"], tmp_path / "x"), ws, LOG, mode="install", home=home)
    kit_writes = [rel for rel in written if rel not in V1["templates"]]
    assert kit_writes[-1] == ".kit/VERSION" and len(kit_writes) > 1


def test_already_up_to_date_downloads_nothing(installed, fake, home):
    harness = Harness(fake)
    code, text = update(installed, fake, home, release=release("0.1.0"))
    assert code == 0 and "Already up to date (v0.1.0)" in text
    assert harness.net.downloads() == []


def test_check_only_and_no_terminal_change_nothing(installed, dists, fake, home):
    harness = Harness(fake)
    harness.net.serve_release("0.2.0", dists["0.2.0"])
    before = tree(installed.root)
    code, text = update(installed, fake, home, release=release(), check_only=True)
    assert code == 0 and "Update available: v0.1.0 -> v0.2.0" in text
    code, text = update(installed, fake, home, release=release(), yes=False,
                        interactive=lambda: False)
    assert code == 0 and "brain update --yes" in text
    assert harness.net.downloads() == [] and tree(installed.root) == before


def test_terminal_prompt(installed, dists, fake, home):
    harness = Harness(fake)
    harness.net.serve_release("0.2.0", dists["0.2.0"])
    before = tree(installed.root)
    code, text = update(installed, fake, home, release=release(), yes=False,
                        interactive=lambda: True, ask=lambda prompt: "n")
    assert code == 0 and "No changes made" in text and tree(installed.root) == before
    code, text = update(installed, fake, home, release=release(), yes=False,
                        interactive=lambda: True, ask=lambda prompt: "y")
    assert code == 0, text
    assert installed.version_file.read_text().strip() == "0.2.0"


def test_checksum_mismatch_changes_nothing(tmp_path, installed, dists, fake, home):
    harness = Harness(fake)
    harness.net.serve_release("0.2.0", dists["0.2.0"])
    bad_sums = write(tmp_path / "bad-sums", "0" * 64 + "  brain-starter-v0.2.0.tar.gz\n")
    harness.net.files["https://example.invalid/dl/v0.2.0/SHA256SUMS"] = bad_sums
    before = tree(installed.root)
    code, text = update(installed, fake, home, release=release())
    assert code == 1 and "didn't match its checksum" in text
    assert tree(installed.root) == before
    assert snapshot_mod.list_snapshots(installed) == [] and harness.apply_calls == []


def test_failed_download_changes_nothing(installed, fake, home):
    harness = Harness(fake)  # nothing served: curl gets a 404
    before = tree(installed.root)
    code, text = update(installed, fake, home, release=release())
    assert code == 1 and "Couldn't download" in text and tree(installed.root) == before


def test_apply_failure_rolls_back_to_the_exact_bytes(installed, dists, fake, home):
    harness = Harness(fake)
    harness.net.serve_release("0.2.0", dists["bad"])
    before = tree(installed.root)
    code, text = update(installed, fake, home, release=release())
    assert code == 1, text
    assert "migration exploded" in text and "put back exactly as it was" in text
    assert tree(installed.root) == before
    assert installed.version_file.read_text().strip() == "0.1.0"
    assert harness.doctor_runs == 1  # no post-update doctor after a failed apply


def test_ctrl_c_during_the_apply_still_rolls_back(installed, dists, fake, home):
    def apply_then_interrupt(argv):
        run_apply_in_process(argv)  # files are written...
        raise KeyboardInterrupt  # ...then she presses Ctrl-C

    harness = Harness(fake, apply=apply_then_interrupt)
    harness.net.serve_release("0.2.0", dists["0.2.0"])
    before = tree(installed.root)
    code, text = update(installed, fake, home, release=release())
    assert code == 1 and "stopped before it finished" in text
    assert tree(installed.root) == before


def test_doctor_regression_rolls_back(installed, dists, fake, home):
    harness = Harness(fake, before=[{"name": "python", "status": "ok"}],
                      after=[{"name": "python", "status": "fail"}])
    harness.net.serve_release("0.2.0", dists["0.2.0"])
    before = tree(installed.root)
    code, text = update(installed, fake, home, release=release())
    assert code == 1 and "python" in text
    assert tree(installed.root) == before


def test_doctor_that_stops_working_rolls_back(installed, dists, fake, home):
    harness = Harness(fake, after="Traceback (most recent call last): ...")
    harness.net.serve_release("0.2.0", dists["0.2.0"])
    before = tree(installed.root)
    code, text = update(installed, fake, home, release=release())
    assert code == 1 and "health check stopped working" in text
    assert tree(installed.root) == before


def test_existing_doctor_failures_do_not_roll_back(installed, dists, fake, home):
    checks = [{"name": "slack", "status": "fail"}, {"name": "python", "status": "ok"}]
    harness = Harness(fake, before=checks, after=checks + [{"name": "time machine", "status": "warn"}])
    harness.net.serve_release("0.2.0", dists["0.2.0"])
    code, text = update(installed, fake, home, release=release())
    assert code == 0, text


def test_doctor_output_after_stray_lines_is_still_read(installed, dists, fake, home):
    noisy = "warning: something chatty\n" + json.dumps([{"name": "python", "status": "fail"}], indent=2)
    harness = Harness(fake, before=[{"name": "python", "status": "ok"}], after=noisy)
    harness.net.serve_release("0.2.0", dists["0.2.0"])
    code, text = update(installed, fake, home, release=release())
    assert code == 1 and "these health checks started failing: python" in text


def test_new_or_renamed_failing_checks_do_not_roll_back(installed, dists, fake, home):
    harness = Harness(fake, before=[{"name": "python", "status": "ok"}],
                      after=[{"name": "python version", "status": "fail"},  # renamed
                             {"name": "brand new check", "status": "fail"}])  # added
    harness.net.serve_release("0.2.0", dists["0.2.0"])
    code, text = update(installed, fake, home, release=release())
    assert code == 0, text
    assert installed.version_file.read_text().strip() == "0.2.0"


def test_no_doctor_baseline_means_no_rollback_on_doctor_results(installed, dists, fake, home):
    harness = Harness(fake, before="Traceback: the old doctor crashed",
                      after=[{"name": "slack", "status": "fail"}, {"name": "python", "status": "fail"}])
    harness.net.serve_release("0.2.0", dists["0.2.0"])
    code, text = update(installed, fake, home, release=release())
    assert code == 0, text


def test_doctor_regressions_rule():
    from brainkit.update import doctor_regressions

    before = {"python": "ok", "slack": "fail", "time machine": "warn"}
    after = {"python": "fail", "slack": "fail", "time machine": "fail", "new": "fail"}
    assert doctor_regressions(before, after) == ["python", "time machine"]
    assert doctor_regressions({}, after) == []


def test_unreadable_doctor_before_is_tolerated(installed, dists, fake, home):
    harness = Harness(fake, before="", after="")
    harness.net.serve_release("0.2.0", dists["0.2.0"])
    code, text = update(installed, fake, home, release=release())
    assert code == 0, text


def test_her_files_identical_after_update_and_after_rollback(installed, dists, fake, home):
    harness = Harness(fake)
    harness.net.serve_release("0.2.0", dists["0.2.0"])
    before = her_files(installed)
    assert update(installed, fake, home, release=release())[0] == 0
    assert her_files(installed) == before
    out = io.StringIO()
    assert rollback(installed, fake, LOG, home=home, out=out) == 0, out.getvalue()
    assert her_files(installed) == before
    assert installed.version_file.read_text().strip() == "0.1.0"


def test_manual_rollback_restores_kit_and_keeps_newer_work(installed, dists, fake, home):
    ws = installed
    harness = Harness(fake)
    harness.net.serve_release("0.2.0", dists["0.2.0"])
    assert update(ws, fake, home, release=release())[0] == 0
    write(ws.root / "tasks/active/after-update.md", "new task\n")
    write(ws.root / "me.md", "edited again after the update\n")
    write(ws.root / "AGENTS.md", "she edited the v2 rules\n")

    out = io.StringIO()
    code = rollback(ws, fake, LOG, home=home, out=out)
    text = out.getvalue()

    assert code == 0, text
    assert "from v0.2.0 to v0.1.0" in text
    assert ws.version_file.read_text().strip() == "0.1.0"
    assert manifest_mod.load(ws.manifest)["version"] == "0.1.0"
    assert (ws.root / "docs/old.md").read_text() == "old\n"
    assert not (ws.root / "docs/new.md").exists()
    assert not (ws.root / ".claude/skills/weekly").exists()
    assert not os.path.lexists(str(ws.agents_skills / "weekly"))
    assert (ws.root / ".claude/skills/brief/SKILL.md").read_text() == "brief v1\n"
    assert (ws.root / "AGENTS.md").read_text() == "she edited the v2 rules\n"
    assert (ws.root / "AGENTS.md.new").read_text() == "rules v1\n"
    assert (ws.root / "docs/guide.md").read_text() == "guide edited by her\n"
    assert not (ws.root / "docs/guide.md.new").exists()
    assert (ws.root / "me.md").read_text() == "edited again after the update\n"
    assert (ws.root / "tasks/active/after-update.md").read_text() == "new task\n"
    assert "AGENTS.md.new" in text


def test_rollback_after_an_interrupted_update_puts_v1_back_cleanly(tmp_path, installed, dists, fake,
                                                                   home, monkeypatch):
    ws = installed
    rebuild_links(ws)  # so the comparison below isn't about links
    before = tree(ws.root)
    snapshot_mod.create(ws, "v0.1.0-to-v0.2.0")  # what `brain update` does first
    real, writes = apply_mod._write_copy, []

    def write_then_die(src, dst, mode):  # the power goes just before VERSION is written
        if dst.name == "VERSION":
            raise KeyboardInterrupt
        writes.append(dst.relative_to(ws.root).as_posix())
        real(src, dst, mode)

    monkeypatch.setattr(apply_mod, "_write_copy", write_then_die)
    with pytest.raises(KeyboardInterrupt):
        apply_release(extract_release(dists["0.2.0"]["tarball"], tmp_path / "x2"), ws, LOG,
                      mode="update", home=home)
    monkeypatch.setattr(apply_mod, "_write_copy", real)
    assert {"AGENTS.md", "docs/new.md", ".claude/skills/weekly/SKILL.md",
            "docs/guide.md.new"} <= set(writes)

    out = io.StringIO()
    code = rollback(ws, fake, LOG, home=home, out=out)
    text = out.getvalue()

    assert code == 0, text
    assert "from v0.2.0 to v0.1.0" in text
    assert tree(ws.root) == before  # v1 back, v2-only files gone, no .new copies anywhere
    assert apply_mod.load_incoming(ws) is None
    assert "kept" not in text.lower()


def test_manual_rollback_never_brings_back_files_the_user_moved_or_deleted(installed, dists, fake,
                                                                           home):
    ws = installed
    write(ws.root / "notes/idea.md", "an idea\n")
    harness = Harness(fake)
    harness.net.serve_release("0.2.0", dists["0.2.0"])
    assert update(ws, fake, home, release=release())[0] == 0
    (ws.root / "tasks/archive").mkdir(parents=True, exist_ok=True)
    os.rename(str(ws.root / "tasks/active/plan.md"), str(ws.root / "tasks/archive/plan.md"))
    (ws.root / "notes/idea.md").unlink()
    (ws.root / "me.md").unlink()

    out = io.StringIO()
    assert rollback(ws, fake, LOG, home=home, out=out) == 0, out.getvalue()

    assert not (ws.root / "tasks/active/plan.md").exists()
    assert (ws.root / "tasks/archive/plan.md").read_text() == "# Plan\n"
    assert not (ws.root / "notes/idea.md").exists()
    assert not (ws.root / "me.md").exists()
    assert (ws.root / "AGENTS.md").read_text() == "rules v1\n"  # the kit itself did come back
    assert ws.version_file.read_text().strip() == "0.1.0"


def test_rollback_list_named_and_missing(installed, dists, fake, home, capsys):
    out = io.StringIO()
    assert rollback(installed, fake, LOG, home=home, out=out) == 1
    assert "no safety copies" in out.getvalue()
    harness = Harness(fake)
    harness.net.serve_release("0.2.0", dists["0.2.0"])
    assert update(installed, fake, home, release=release())[0] == 0
    name = snapshot_mod.list_snapshots(installed)[0].name
    from brainkit.cli import main as cli_main

    assert cli_main(["--workspace", str(installed.root), "rollback", "--list"]) == 0
    assert name in capsys.readouterr().out
    out = io.StringIO()
    assert rollback(installed, fake, LOG, snapshot="nope", home=home, out=out) == 1
    out = io.StringIO()
    assert rollback(installed, fake, LOG, snapshot=name[:-4], home=home, out=out) == 0


def test_update_refuses_while_another_task_holds_the_lock(installed, fake, home):
    Harness(fake)
    with try_lock(installed.lock_file) as held:
        assert held
        code, text = update(installed, fake, home, release=release())
        assert code == 1 and BUSY in text
        out = io.StringIO()
        assert rollback(installed, fake, LOG, home=home, out=out) == 1


# --- snapshots -----------------------------------------------------------------------------

def test_snapshot_skips_generated_and_unrestorable_paths(ws, tmp_path):
    for rel in ("me.md", "logs/brain.log", "reports/r.zip", ".kit/cache/x", ".kit/snapshots/old.tgz",
                ".kit/run.lock", ".kit/lib/brainkit/__pycache__/x.pyc", ".claude/skills/a/SKILL.md"):
        write(ws.root / rel, "x")
    os.symlink(str(tmp_path), str(ws.root / "outside-link"))
    ws.agents_skills.mkdir(parents=True)
    os.symlink("../../.claude/skills/a", str(ws.agents_skills / "a"))
    snap = snapshot_mod.create(ws, "v0.1.0-to-v0.2.0", when=datetime(2026, 10, 13, 9, 30))
    assert snap.name == "20261013T093000Z-v0.1.0-to-v0.2.0.tgz"
    from brainkit.safe_tar import member_names

    names = set(member_names(snap))
    assert {"me.md", ".claude/skills/a/SKILL.md", ".agents/skills/a"} <= names
    for rel in ("logs/brain.log", "reports/r.zip", ".kit/cache/x", ".kit/snapshots/old.tgz",
                ".kit/run.lock", ".kit/lib/brainkit/__pycache__/x.pyc", "outside-link"):
        assert rel not in names


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read everything")
def test_snapshot_skips_unreadable_files_instead_of_failing(ws):
    secret = write(ws.root / "locked.md", "x")
    os.chmod(str(secret), 0)
    try:
        snap = snapshot_mod.create(ws, "label")
    finally:
        os.chmod(str(secret), 0o644)
    from brainkit.safe_tar import member_names

    assert "locked.md" not in member_names(snap)


def test_snapshots_are_private(ws):
    write(ws.root / "briefs/2026-10-13.md", "private brief\n")
    ws.snapshots.mkdir(parents=True)
    older = write(ws.snapshots / "20261001T080000Z-v0.0.9-to-v0.1.0.tgz", b"", mode=0o644)
    old_umask = os.umask(0o002)  # a permissive umask must not leak into snapshots
    try:
        snap = snapshot_mod.create(ws, "v0.1.0-to-v0.2.0")
    finally:
        os.umask(old_umask)
    assert os.stat(snap).st_mode & 0o777 == 0o600
    assert os.stat(ws.snapshots).st_mode & 0o777 == 0o700
    assert os.stat(older).st_mode & 0o777 == 0o600


def test_snapshot_names_are_utc_so_clock_changes_sort_right(ws):
    london = tz("Europe/London")
    # 25 Oct 2026: clocks go back at 02:00 BST, so 01:10 happens twice.
    first = datetime(2026, 10, 25, 1, 50, tzinfo=london, fold=0)  # 00:50 UTC
    second = datetime(2026, 10, 25, 1, 10, tzinfo=london, fold=1)  # 01:10 UTC, later
    a = snapshot_mod.create(ws, "first", when=first)
    b = snapshot_mod.create(ws, "second", when=second)
    assert a.name == "20261025T005000Z-first.tgz" and b.name == "20261025T011000Z-second.tgz"
    assert snapshot_mod.list_snapshots(ws)[0] == b


def test_old_local_time_snapshot_names_are_still_listed(ws):
    new = snapshot_mod.create(ws, "new", when=datetime(2026, 10, 13, 12, 0))
    old = ws.snapshots / "20261001-090000-v0.0.9-to-v0.1.0.tgz"
    shutil.copyfile(str(new), str(old))
    names = [p.name for p in snapshot_mod.list_snapshots(ws)]
    assert names == [new.name, old.name]
    assert snapshot_mod.snapshot_time(old.name) is not None


def test_snapshots_keep_the_newest_three(ws):
    made = [snapshot_mod.create(ws, f"v0.{i}.0-to-v0.{i + 1}.0", when=datetime(2026, 10, i + 1, 8, 0))
            for i in range(5)]
    left = snapshot_mod.list_snapshots(ws)
    assert [p.name for p in left] == [p.name for p in reversed(made[2:])]
    same_second = snapshot_mod.create(ws, "again", when=datetime(2026, 10, 9, 8, 0))
    twin = snapshot_mod.create(ws, "again", when=datetime(2026, 10, 9, 8, 0))
    assert twin.name != same_second.name and twin.exists()


# --- the scheduled check -------------------------------------------------------------------

def job_ctx(ws, fake, at):
    return JobContext(ws=ws, config=Config(), runner=fake, now=at(2026, 10, 13, 9, 0),
                      runs=RunsLedger(ws), log=LOG, notify=lambda title, message: None)


def test_update_check_job_reports_an_available_update(installed, fake, at):
    net = FakeNet(fake)
    net.api[API] = (200, github_release("0.2.0", notes="n" * 900))
    result = update_check.run(job_ctx(installed, fake, at))
    assert (result.status, result.summary) == ("ok", "update available: v0.2.0")
    assert result.data["current"] == "0.1.0" and result.data["latest"] == "0.2.0"
    assert result.data["update_available"] is True and len(result.data["notes"]) == 500
    assert result.data["html_url"].endswith("/tag/v0.2.0")


def test_update_check_job_up_to_date_and_errors(installed, fake, at):
    net = FakeNet(fake)
    net.api[API] = (200, github_release("0.1.0"))
    result = update_check.run(job_ctx(installed, fake, at))
    assert (result.status, result.summary) == ("ok", "up to date")
    assert result.data["update_available"] is False
    net.api.clear()  # 404: nothing published yet
    assert update_check.run(job_ctx(installed, fake, at)).summary == "up to date"
    offline = type(fake)()  # every command fails, like curl without a network
    result = update_check.run(job_ctx(installed, offline, at))
    assert result.status == "error" and len(result.detail) <= 200


# --- end to end with the real kit ----------------------------------------------------------

def real_source(dest, version, extra=None):
    shutil.copytree(str(REPO / "kit"), str(dest / "kit"),
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"))
    (dest / "kit" / ".kit" / "VERSION").write_text(version + "\n")
    for name in ("templates", "migrations"):
        shutil.copytree(str(REPO / name), str(dest / name),
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copyfile(str(REPO / "install.sh"), str(dest / "install.sh"))
    for rel, text in (extra or {}).items():
        write(dest / "kit" / rel, text)
    return dest


def test_end_to_end_with_the_real_applier_subprocess(tmp_path, ws, fake, home):
    a = build(real_source(tmp_path / "a", "9.0.0"), tmp_path / "dist-a")
    b = build(real_source(tmp_path / "b", "9.0.1", {"docs/e2e-check.md": "added in 9.0.1\n"}),
              tmp_path / "dist-b")
    apply_release(extract_release(a["tarball"], tmp_path / "xa"), ws, LOG, mode="install", home=home)
    write(ws.root / "tasks/active/mine.md", "hers\n")

    def real_apply(argv):
        env = fake.calls[-1]["env"]
        return Runner().run(argv, env=env, timeout=300, cwd=str(ws.root))

    harness = Harness(fake, apply=real_apply)
    harness.net.serve_release("9.0.1", b)
    code, text = update(ws, fake, home, release=release("9.0.1"))
    assert code == 0, text
    assert ws.version_file.read_text().strip() == "9.0.1"
    assert (ws.root / "docs/e2e-check.md").read_text() == "added in 9.0.1\n"
    assert (ws.root / "tasks/active/mine.md").read_text() == "hers\n"
