from __future__ import annotations

import json
import logging
import os

import pytest

from brainkit import apply as apply_mod
from brainkit import manifest as manifest_mod
from brainkit.apply import ApplyError, MigrationError, apply_release, rebuild_links
from brainkit.cli import main as cli_main
from release_helpers import make_release_dir, tree, write

LOG = logging.getLogger("test-apply")

ORDER_MIGRATION = (
    "from pathlib import Path\n\n"
    "def migrate(workspace):\n"
    "    log = Path(workspace) / 'order.log'\n"
    "    log.write_text((log.read_text() if log.exists() else '') + '{mid}\\n')\n"
    "    return 'ran {mid}'\n"
)


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    path = tmp_path / "home"
    path.mkdir()
    monkeypatch.setenv("HOME", str(path))
    monkeypatch.delenv("BRAIN_HOME", raising=False)
    return path


def rel_dir(tmp_path, name, version, **kwargs):
    return make_release_dir(tmp_path / f"rel-{name}", version, **kwargs)


V1_FILES = {
    "AGENTS.md": "v1 rules\n",
    "docs/plain.md": "plain v1\n",
    "docs/edited.md": "edited v1\n",
    "docs/same.md": "same\n",
    "docs/dropped.md": "dropped\n",
    "docs/dropped-edited.md": "dropped edited\n",
    ".claude/skills/old-skill/SKILL.md": "old skill\n",
    ".claude/skills/brief/SKILL.md": "brief v1\n",
    ".kit/bin/helper": "#!/bin/sh\necho v1\n",
}
V2_FILES = {
    "AGENTS.md": "v2 rules\n",
    "docs/plain.md": "plain v2\n",
    "docs/edited.md": "edited v2\n",
    "docs/same.md": "same\n",
    "docs/new.md": "new\n",
    ".claude/skills/brief/SKILL.md": "brief v2\n",
    ".kit/bin/helper": "#!/bin/sh\necho v2\n",
}
TEMPLATES = {"me.md": "# About me\n", ".kit/config.json": "{}\n", "tasks/active/.keep": ""}


def test_fresh_install_writes_kit_templates_links_and_marks_migrations(tmp_path, ws, home):
    rel = rel_dir(tmp_path, "v1", "0.1.0", files=V1_FILES, templates=TEMPLATES,
                  migrations={"0002_first.py": ORDER_MIGRATION.format(mid="0002_first")})
    report = apply_release(rel, ws, LOG, mode="install", home=home)

    new = manifest_mod.load(rel / "manifest.json")
    assert sorted(report.added) == sorted(new["kit_files"])
    assert report.replaced == report.kept_modified == report.removed == []
    assert sorted(report.templates_created) == sorted(TEMPLATES)
    assert report.links == ["brief", "old-skill"]
    assert report.migrations_run == []
    assert not (ws.root / "order.log").exists()  # marked, not run
    state = json.loads((ws.state / "migrations.json").read_text())
    assert state["applied"]["0002_first"]["ran"] is False
    assert manifest_mod.load(ws.manifest) == new
    assert ws.version_file.read_text().strip() == "0.1.0"
    assert os.stat(ws.root / ".kit/bin/helper").st_mode & 0o777 == 0o755
    assert os.stat(ws.root / "AGENTS.md").st_mode & 0o777 == 0o644
    assert os.readlink(str(ws.agents_skills / "brief")) == "../../.claude/skills/brief"
    assert (ws.agents_skills / "brief" / "SKILL.md").read_text() == "brief v1\n"


def test_install_over_existing_files_keeps_hers(tmp_path, ws, home):
    write(ws.root / "AGENTS.md", "my own rules\n")
    write(ws.root / "docs/same.md", "same\n")  # identical to ours
    write(ws.root / "me.md", "my notes\n")  # a template path she already has
    rel = rel_dir(tmp_path, "v1", "0.1.0", files=V1_FILES, templates=TEMPLATES)
    report = apply_release(rel, ws, LOG, mode="install", home=home)
    assert report.kept_modified == ["AGENTS.md"]
    assert (ws.root / "AGENTS.md").read_text() == "my own rules\n"
    assert (ws.root / "AGENTS.md.new").read_text() == "v1 rules\n"
    assert "docs/same.md" in report.unchanged
    assert "me.md" not in report.templates_created
    assert (ws.root / "me.md").read_text() == "my notes\n"


def test_update_covers_every_branch(tmp_path, ws, home):
    v1 = rel_dir(tmp_path, "v1", "0.1.0", files=V1_FILES, templates=TEMPLATES)
    apply_release(v1, ws, LOG, mode="install", home=home)
    write(ws.root / "docs/edited.md", "edited by her\n")
    write(ws.root / "docs/dropped-edited.md", "she changed this\n")
    write(ws.root / "me.md", "her answers\n")
    v2 = rel_dir(tmp_path, "v2", "0.2.0", files=V2_FILES,
                 templates=dict(TEMPLATES, **{"voice.md": "# Voice\n"}))

    report = apply_release(v2, ws, LOG, mode="update", home=home)

    assert sorted(report.replaced) == [".claude/skills/brief/SKILL.md", ".kit/VERSION",
                                       ".kit/bin/helper", "AGENTS.md", "docs/plain.md"]
    assert report.added == ["docs/new.md"]
    assert report.kept_modified == ["docs/edited.md"]
    assert sorted(report.unchanged) == [".kit/bin/brain", "docs/same.md"]
    assert sorted(report.removed) == [".claude/skills/old-skill/SKILL.md", "docs/dropped.md"]
    assert report.kept_removed_modified == ["docs/dropped-edited.md"]
    assert report.templates_created == ["voice.md"]
    assert (ws.root / "docs/edited.md").read_text() == "edited by her\n"
    assert (ws.root / "docs/edited.md.new").read_text() == "edited v2\n"
    assert (ws.root / "docs/dropped-edited.md").read_text() == "she changed this\n"
    assert not (ws.root / "docs/dropped.md").exists()
    assert not (ws.root / ".claude/skills/old-skill").exists()  # emptied folder pruned
    assert not os.path.lexists(str(ws.agents_skills / "old-skill"))
    assert (ws.root / "me.md").read_text() == "her answers\n"
    assert ws.version_file.read_text().strip() == "0.2.0"
    assert manifest_mod.load(ws.manifest)["version"] == "0.2.0"
    assert os.stat(ws.root / ".kit/bin/helper").st_mode & 0o777 == 0o755


def test_new_beside_an_executable_keeps_its_mode(tmp_path, ws, home):
    apply_release(rel_dir(tmp_path, "v1", "0.1.0", files=V1_FILES), ws, LOG, mode="install", home=home)
    write(ws.root / ".kit/bin/helper", "#!/bin/sh\necho mine\n")
    os.chmod(str(ws.root / ".kit/bin/helper"), 0o755)
    apply_release(rel_dir(tmp_path, "v2", "0.2.0", files=V2_FILES), ws, LOG, home=home)
    assert os.stat(ws.root / ".kit/bin/helper.new").st_mode & 0o777 == 0o755


def test_unchanged_file_gets_its_mode_back(tmp_path, ws, home):
    rel = rel_dir(tmp_path, "v1", "0.1.0", files=V1_FILES)
    apply_release(rel, ws, LOG, mode="install", home=home)
    os.chmod(str(ws.root / ".kit/bin/helper"), 0o644)
    report = apply_release(rel, ws, LOG, home=home)
    assert ".kit/bin/helper" in report.unchanged
    assert os.stat(ws.root / ".kit/bin/helper").st_mode & 0o777 == 0o755


def test_a_folder_where_a_kit_file_belongs_is_left_alone(tmp_path, ws, home):
    (ws.root / "AGENTS.md").mkdir()
    report = apply_release(rel_dir(tmp_path, "v1", "0.1.0", files=V1_FILES), ws, LOG,
                           mode="install", home=home)
    assert "AGENTS.md" in report.kept_modified
    assert (ws.root / "AGENTS.md").is_dir()
    assert (ws.root / "AGENTS.md.new").read_text() == "v1 rules\n"


def test_rebuild_links(tmp_path, ws):
    skills = ws.claude_skills
    for name in ("brief", "my-notes"):
        write(skills / name / "SKILL.md", name)
    write(skills / "drafts" / "notes.md", "no SKILL.md here")
    write(skills / "lost" / "README.md", "lost its SKILL.md")
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    links = ws.agents_skills
    links.mkdir(parents=True)
    os.symlink("../../.claude/skills/gone", str(links / "gone"))  # dangling
    os.symlink("../../.claude/skills/lost", str(links / "lost"))  # skill without SKILL.md
    os.symlink(str(outside), str(links / "external"))  # hers, valid, elsewhere
    os.symlink("../../.claude/skills/my-notes", str(links / "brief"))  # wrong target
    write(links / "notes.txt", "hers")
    (links / "custom").mkdir()

    linked = rebuild_links(ws)

    assert linked == ["brief", "my-notes"]
    assert os.readlink(str(links / "brief")) == "../../.claude/skills/brief"
    assert os.readlink(str(links / "my-notes")) == "../../.claude/skills/my-notes"
    assert not os.path.lexists(str(links / "gone"))
    assert not os.path.lexists(str(links / "lost"))
    assert not os.path.lexists(str(links / "drafts"))
    assert os.readlink(str(links / "external")) == str(outside)
    assert (links / "notes.txt").read_text() == "hers" and (links / "custom").is_dir()


def test_rebuild_links_never_replaces_a_real_folder(ws):
    write(ws.claude_skills / "brief" / "SKILL.md", "x")
    write(ws.agents_skills / "brief" / "SKILL.md", "her own copy")
    assert rebuild_links(ws) == []
    assert (ws.agents_skills / "brief" / "SKILL.md").read_text() == "her own copy"


def test_migrations_run_once_in_order(tmp_path, ws, home):
    apply_release(rel_dir(tmp_path, "v1", "0.1.0", files=V1_FILES), ws, LOG, mode="install", home=home)
    migrations = {"0003_b.py": ORDER_MIGRATION.format(mid="0003_b"),
                  "0002_a.py": ORDER_MIGRATION.format(mid="0002_a")}
    v2 = rel_dir(tmp_path, "v2", "0.2.0", files=V2_FILES, migrations=migrations)
    report = apply_release(v2, ws, LOG, mode="update", home=home)
    assert report.migrations_run == ["0002_a", "0003_b"]
    assert (ws.root / "order.log").read_text() == "0002_a\n0003_b\n"
    state = json.loads((ws.state / "migrations.json").read_text())
    assert state["applied"]["0003_b"]["ran"] is True
    assert state["applied"]["0003_b"]["message"] == "ran 0003_b"
    again = apply_release(v2, ws, LOG, mode="update", home=home)
    assert again.migrations_run == []
    assert (ws.root / "order.log").read_text() == "0002_a\n0003_b\n"


def test_install_over_an_existing_brain_runs_pending_migrations(tmp_path, ws, home):
    apply_release(rel_dir(tmp_path, "v1", "0.1.0", files=V1_FILES), ws, LOG, mode="install", home=home)
    v2 = rel_dir(tmp_path, "v2", "0.2.0", files=V2_FILES,
                 migrations={"0002_a.py": ORDER_MIGRATION.format(mid="0002_a")})
    report = apply_release(v2, ws, LOG, mode="install", home=home)
    assert report.migrations_run == ["0002_a"]


def test_failing_migration_aborts_before_the_new_manifest(tmp_path, ws, home):
    apply_release(rel_dir(tmp_path, "v1", "0.1.0", files=V1_FILES), ws, LOG, mode="install", home=home)
    bad = "def migrate(workspace):\n    raise RuntimeError('boom')\n"
    v2 = rel_dir(tmp_path, "v2", "0.2.0", files=V2_FILES, migrations={"0002_bad.py": bad})
    with pytest.raises(MigrationError, match="boom"):
        apply_release(v2, ws, LOG, mode="update", home=home)
    assert manifest_mod.load(ws.manifest)["version"] == "0.1.0"
    assert "0002_bad" not in json.loads((ws.state / "migrations.json").read_text())["applied"]
    assert apply_mod.load_incoming(ws)["version"] == "0.2.0"  # the caller's rollback clears it


def interrupt_apply(release, ws, home, monkeypatch, after_writes):
    """Run an update that is killed after `after_writes` file writes (power cut, kill -9)."""
    real, writes = apply_mod._write_copy, []

    def write_then_die(src, dst, mode):
        if len(writes) >= after_writes:
            raise KeyboardInterrupt
        writes.append(dst)
        real(src, dst, mode)

    monkeypatch.setattr(apply_mod, "_write_copy", write_then_die)
    with pytest.raises(KeyboardInterrupt):
        apply_release(release, ws, LOG, mode="update", home=home)
    monkeypatch.setattr(apply_mod, "_write_copy", real)
    return writes


def test_incoming_manifest_is_recorded_before_any_write_and_cleared_after(tmp_path, ws, home,
                                                                         monkeypatch):
    apply_release(rel_dir(tmp_path, "v1", "0.1.0", files=V1_FILES), ws, LOG, mode="install", home=home)
    seen = []
    real = apply_mod._write_copy

    def spy(src, dst, mode):
        seen.append(apply_mod.load_incoming(ws))
        real(src, dst, mode)

    monkeypatch.setattr(apply_mod, "_write_copy", spy)
    apply_release(rel_dir(tmp_path, "v2", "0.2.0", files=V2_FILES), ws, LOG, home=home)
    assert seen and all(m is not None and m["version"] == "0.2.0" for m in seen)
    assert apply_mod.load_incoming(ws) is None
    assert not apply_mod.incoming_path(ws).exists()


def test_next_apply_treats_an_interrupted_applys_files_as_kit_content(tmp_path, ws, home,
                                                                     monkeypatch):
    apply_release(rel_dir(tmp_path, "v1", "0.1.0", files=V1_FILES), ws, LOG, mode="install", home=home)
    write(ws.root / "docs/edited.md", "edited by her\n")
    v2 = rel_dir(tmp_path, "v2", "0.2.0", files=V2_FILES)
    written = interrupt_apply(v2, ws, home, monkeypatch, after_writes=5)
    assert ws.root / "docs/new.md" in written and ws.root / "docs/plain.md" not in written
    assert manifest_mod.load(ws.manifest)["version"] == "0.1.0"
    assert (ws.root / "AGENTS.md").read_text() == "v2 rules\n"

    v3_files = dict(V2_FILES, **{"AGENTS.md": "v3 rules\n", "docs/plain.md": "plain v3\n"})
    del v3_files["docs/new.md"]
    report = apply_release(rel_dir(tmp_path, "v3", "0.3.0", files=v3_files), ws, LOG, home=home)

    assert report.kept_modified == ["docs/edited.md"]  # only her real edit
    assert "AGENTS.md" in report.replaced and "docs/plain.md" in report.replaced
    assert "docs/new.md" in report.removed and not (ws.root / "docs/new.md").exists()
    assert (ws.root / "AGENTS.md").read_text() == "v3 rules\n"
    assert not (ws.root / "AGENTS.md.new").exists()
    assert apply_mod.load_incoming(ws) is None
    assert manifest_mod.load(ws.manifest)["version"] == "0.3.0"


def test_her_files_are_never_touched(tmp_path, ws, home):
    apply_release(rel_dir(tmp_path, "v1", "0.1.0", files=V1_FILES, templates=TEMPLATES), ws, LOG,
                  mode="install", home=home)
    hers = {
        "me.md": "filled in by onboarding\n",
        ".kit/config.json": '{"brief_time": "08:15"}\n',
        "tasks/active/plan.md": "# Plan\n",
        "tasks/archive/old.md": "done\n",
        ".claude/skills/my-sales/SKILL.md": "her skill\n",
        ".claude/settings.local.json": "{}\n",
        "briefs/2026-10-13.md": "brief\n",
        "ledgers/runs.jsonl": '{"job": "brief"}\n',
        "docs/edited.md": "her edit of a kit file\n",
        "docs/dropped-edited.md": "her edit of a dropped kit file\n",
    }
    for rel, text in hers.items():
        write(ws.root / rel, text)
    v2 = rel_dir(tmp_path, "v2", "0.2.0", files=V2_FILES,
                 templates=dict(TEMPLATES, **{"me.md": "# New template\n", "voice.md": "v\n"}))
    kit_paths = set(manifest_mod.load(ws.manifest)["kit_files"]) | set(
        manifest_mod.load(v2 / "manifest.json")["kit_files"])

    def her_view():
        return {rel: value for rel, value in tree(ws.root).items()
                if rel not in kit_paths - set(hers) and not rel.endswith(".new")
                and not rel.startswith((".kit/manifest.json", ".kit/state", ".agents/"))
                and value != "dir" and rel != "voice.md"}

    before = her_view()
    apply_release(v2, ws, LOG, mode="update", home=home)
    assert her_view() == before
    for rel, text in hers.items():
        assert (ws.root / rel).read_text() == text


def test_damaged_release_is_refused_before_writing(tmp_path, ws, home):
    rel = rel_dir(tmp_path, "v1", "0.1.0", files=V1_FILES)
    write(rel / "kit" / "AGENTS.md", "tampered\n")
    before = tree(ws.root)
    with pytest.raises(ApplyError, match="damaged"):
        apply_release(rel, ws, LOG, mode="install", home=home)
    assert tree(ws.root) == before


def test_manifest_path_outside_the_workspace_is_refused(tmp_path, ws, home):
    rel = rel_dir(tmp_path, "v1", "0.1.0", files=V1_FILES)
    data = manifest_mod.load(rel / "manifest.json")
    data["kit_files"]["../escape.md"] = data["kit_files"]["AGENTS.md"]
    (rel / "manifest.json").write_text(json.dumps(data))
    with pytest.raises(ApplyError):
        apply_release(rel, ws, LOG, mode="install", home=home)
    assert not (ws.root.parent / "escape.md").exists()


def test_codex_rules_are_copied_to_home(tmp_path, ws, home):
    files = dict(V1_FILES, **{".kit/codex/brain-starter.rules": "prefix_rule(...)\n"})
    write(home / ".codex" / "rules" / "other.rules", "hers\n")
    rel = rel_dir(tmp_path, "v1", "0.1.0", files=files)
    report = apply_release(rel, ws, LOG, mode="install", home=home)
    target = home / ".codex" / "rules" / "brain-starter.rules"
    assert report.codex_rules == "written"
    assert target.read_text() == "prefix_rule(...)\n"
    assert (home / ".codex" / "rules" / "other.rules").read_text() == "hers\n"
    assert apply_release(rel, ws, LOG, home=home).codex_rules == "unchanged"


def test_no_codex_rules_file_means_no_codex_folder(tmp_path, ws, home):
    report = apply_release(rel_dir(tmp_path, "v1", "0.1.0", files=V1_FILES), ws, LOG,
                           mode="install", home=home)
    assert report.codex_rules == ""
    assert not (home / ".codex").exists()


def test_cli_json_success_and_migration_failure(tmp_path, ws, home, capsys):
    rel = rel_dir(tmp_path, "v1", "0.1.0", files=V1_FILES, templates=TEMPLATES)
    code = cli_main(["_apply", "--release-dir", str(rel), "--workspace", str(ws.root),
                     "--mode", "install", "--json"])
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert code == 0 and payload["ok"] is True
    assert payload["report"]["version"] == "0.1.0" and "AGENTS.md" in payload["report"]["added"]

    bad = "def migrate(workspace):\n    raise ValueError('nope')\n"
    v2 = rel_dir(tmp_path, "v2", "0.2.0", files=V2_FILES, migrations={"0002_bad.py": bad})
    code = cli_main(["--workspace", str(ws.root), "_apply", "--release-dir", str(v2), "--json"])
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert code == 3 and payload == {"ok": False, "kind": "migration",
                                     "error": "migration 0002_bad failed: nope"}


def test_apply_is_hidden_from_help(capsys):
    with pytest.raises(SystemExit):
        cli_main(["--help"])
    assert "_apply" not in capsys.readouterr().out


def test_report_round_trips_and_ignores_unknown_fields():
    report = apply_mod.ApplyReport(version="0.2.0", kept_modified=["AGENTS.md"])
    data = dict(report.to_dict(), future_field=1)
    again = apply_mod.ApplyReport.from_dict(data)
    assert again == report
    assert any("AGENTS.md.new" in line for line in again.summary_lines())
