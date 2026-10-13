"""Content checks: skills, Claude settings, Codex rules, Slack manifest, templates and docs.

These guard the files the AI and the user read. A settings file that fails validation is
silently ignored by `claude -p`, so the settings checks here are the cheap first line of defence
(the real check is `claude doctor` on the Mac dry run).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
KIT = REPO / "kit"
TEMPLATES = REPO / "templates"
SKILLS = KIT / ".claude" / "skills"
SETTINGS = KIT / ".claude" / "settings.json"
CODEX_RULES = KIT / ".kit" / "codex" / "brain-starter.rules"
SLACK_MANIFEST = KIT / ".kit" / "slack-manifest.yaml"
HOOK_COMMAND = "${CLAUDE_PROJECT_DIR}/.kit/bin/brain-guard"

DENY = [
    "Bash(gws gmail users messages send *)",
    "Bash(gws gmail users drafts send *)",
    "Bash(gws * +send *)",
    "Bash(gws * +send)",
    "Bash(gws * +reply *)",
    "Bash(gws * +reply)",
    "Bash(gws * +reply-all *)",
    "Bash(gws * +forward *)",
    "Bash(gws auth export *)",
    "Bash(security find-generic-password *)",
    "Bash(security dump-keychain *)",
    "Read(~/.config/gws/**)",
    "Read(~/.claude/.credentials.json)",
    "Read(~/.codex/auth.json)",
    "Read(~/Library/Keychains/**)",
]
ALLOW = [
    "Bash(brain doctor *)",
    "Bash(brain slack recent *)",
    "Bash(gws gmail +triage *)",
    "Bash(gws gmail +read *)",
    "Bash(gws calendar +agenda *)",
    "Bash(gws drive files list *)",
    "Bash(gws drive files get *)",
    "Bash(gws drive files export *)",
]
SLACK_SCOPES = [
    "channels:history", "channels:read", "groups:history", "groups:read", "im:history", "im:read",
    "mpim:history", "mpim:read", "users:read", "search:read",
]

# The `brain` command surface that kit docs and skills may mention.
# command -> {subcommand (None when there is none) -> allowed flags}
CLI = {
    "doctor": {None: {"--json", "--quick"}},
    "catchup": {None: {"--dry-run", "--job"}},
    "brief": {None: {"--no-draft", "--detach"}},
    "schedule": {"install": set(), "uninstall": set(), "status": set()},
    "notify-test": {None: set()},
    "secret": {"set": {"--gui"}, "check": set(), "delete": set(), "list": set()},
    "google": {"connect": {"--client-file"}, "status": set()},
    "draft": {"email": {"--to", "--cc", "--subject", "--body-file", "--body", "--reply-to"}},
    "slack": {
        "whoami": set(), "sync": {"--lookback-days"},
        "recent": {"--hours", "--dms", "--mentions", "--json"}, "search": {"--count"},
        "thread": set(), "channels": set(), "create-app": set(),
    },
    "report": {None: set()},
    "update": {None: {"--check", "--yes"}},
    "rollback": {None: {"--list", "--snapshot"}},
    "tools": {"install": set(), "status": set()},
}
SECRET_NAMES = {"slack", "claude-oauth-token", "airwallex-client-id", "airwallex-key"}
JOBS = {"health", "slack_sync", "update_check", "brief", "weekly_review"}
SHELL_FENCES = {"", "bash", "sh", "zsh", "shell", "console"}


def kit_markdown():
    return sorted(p for p in KIT.rglob("*.md") if "__pycache__" not in p.parts)


def skill_dirs():
    return sorted(p for p in SKILLS.iterdir() if p.is_dir())


def frontmatter(text: str):
    """(fields, body) for a file starting with a `---` block of simple `key: value` lines."""
    match = re.match(r"\A---\n(.*?)\n---\n", text, re.S)
    if not match:
        return None, text
    fields = {}
    for line in match.group(1).splitlines():
        key, sep, value = line.partition(":")
        assert sep and re.match(r"^[a-z][a-z-]*$", key), f"not a simple key: {line!r}"
        fields[key] = value.strip()
    return fields, text[match.end():]


# ---------------------------------------------------------------- skills


@pytest.mark.parametrize("folder", skill_dirs(), ids=lambda p: p.name)
def test_skill_frontmatter(folder):
    path = folder / "SKILL.md"
    assert path.is_file(), f"{folder.name} has no SKILL.md"
    fields, body = frontmatter(path.read_text(encoding="utf-8"))
    assert fields is not None, f"{folder.name}: SKILL.md must start with YAML frontmatter"
    assert set(fields) == {"name", "description"}, "only name and description (portable to Codex)"
    assert fields["name"] == folder.name
    assert re.match(r"^[a-z0-9]+(-[a-z0-9]+)*$", fields["name"])
    assert fields["description"], "description must not be empty"
    assert len(fields["description"]) <= 1024
    for value in fields.values():  # plain YAML scalars: strict parsers (Codex) reject these
        assert ": " not in value and " #" not in value, f"quote-worthy YAML in {value!r}"
        assert value[:1] not in set("[]{}&*!|>'\"%@`#,?:-"), f"YAML indicator at start of {value!r}"
    assert len(body.strip().splitlines()) <= 125, "keep skill bodies to about 120 lines"


def test_yaml_files_parse():
    yaml = pytest.importorskip("yaml")
    manifest = yaml.safe_load(SLACK_MANIFEST.read_text(encoding="utf-8"))
    assert sorted(manifest["oauth_config"]["scopes"]["user"]) == sorted(SLACK_SCOPES)
    assert manifest["settings"]["token_rotation_enabled"] is False
    for folder in skill_dirs():
        text = (folder / "SKILL.md").read_text(encoding="utf-8")
        block = re.match(r"\A---\n(.*?)\n---\n", text, re.S).group(1)
        assert set(yaml.safe_load(block)) == {"name", "description"}, folder.name


def test_agents_md_lists_exactly_the_kit_skills():
    text = (KIT / "AGENTS.md").read_text(encoding="utf-8")
    section = text.split("## Skills", 1)[1].split("\n## ", 1)[0]
    listed = set(re.findall(r"^- `([a-z0-9-]+)`", section, re.M))
    assert listed == {p.name for p in skill_dirs()}
    assert len(text.splitlines()) <= 125, "AGENTS.md should stay about 120 lines"


def test_claude_md_imports_agents_md():
    lines = (KIT / "CLAUDE.md").read_text(encoding="utf-8").splitlines()
    assert lines[0] == "@AGENTS.md"


# ---------------------------------------------------------------- Claude settings


def test_settings_allow_list_never_grants_state_changing_brain_commands():
    allow = json.loads(SETTINGS.read_text(encoding="utf-8"))["permissions"]["allow"]
    assert "Bash(brain *)" not in allow  # an unattended or injected run must not reach _apply etc.
    for rule in allow:
        for risky in ("_apply", "update --yes", "secret set", "secret delete", "rollback", "install"):
            assert risky not in rule, rule


def test_settings_json_parses_and_denies_sends():
    raw = SETTINGS.read_text(encoding="utf-8")
    data = json.loads(raw)
    permissions = data["permissions"]
    missing_deny = [rule for rule in DENY if rule not in permissions["deny"]]
    missing_allow = [rule for rule in ALLOW if rule not in permissions["allow"]]
    assert not missing_deny, missing_deny
    assert not missing_allow, missing_allow
    assert "disableBypassPermissionsMode" not in raw, "untested with T3 Full access; leave it out"


def test_settings_hook_points_at_the_guard():
    data = json.loads(SETTINGS.read_text(encoding="utf-8"))
    entries = data["hooks"]["PreToolUse"]
    commands = []
    for entry in entries:
        tools = set(entry.get("matcher", "").split("|"))
        for hook in entry.get("hooks", []):
            if hook.get("type") == "command":
                commands.append((tools, hook.get("command"), hook.get("args")))
    match = [c for c in commands if c[1] == HOOK_COMMAND]
    assert match, f"no PreToolUse hook runs {HOOK_COMMAND}"
    tools, _, args = match[0]
    assert {"Bash", "Read", "Edit", "Write"} <= tools
    assert args == [], "exec form: no shell, so a space in the home folder can't break it"
    guard = KIT / HOOK_COMMAND.replace("${CLAUDE_PROJECT_DIR}/", "")
    assert guard.is_file(), f"hook target missing: {guard}"


# ---------------------------------------------------------------- Codex and Slack


def test_codex_rules_forbid_sends():
    rules = []
    for line in CODEX_RULES.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        assert stripped.startswith("prefix_rule(") and stripped.endswith(")"), stripped
        assert stripped.count("(") == stripped.count(")")
        assert stripped.count("[") == stripped.count("]")
        assert re.search(r'decision\s*=\s*"forbidden"', stripped), stripped
        rules.append(stripped)
    assert any('"+send"' in rule and '"gmail"' in rule for rule in rules)
    assert any(re.search(r'"users",\s*"messages",\s*"send"', rule) for rule in rules)
    assert any(re.search(r'"users",\s*"drafts",\s*"send"', rule) for rule in rules)


def test_slack_manifest_is_read_only_user_token():
    text = SLACK_MANIFEST.read_text(encoding="utf-8")
    user_block = text.split("    user:\n", 1)[1].split("\nsettings:", 1)[0]
    scopes = re.findall(r"^\s+- ([a-z.:]+)\s*$", user_block, re.M)
    assert sorted(scopes) == sorted(SLACK_SCOPES)
    for setting in ("token_rotation_enabled: false", "org_deploy_enabled: false",
                    "socket_mode_enabled: false"):
        assert re.search(r"^\s+" + re.escape(setting) + r"\s*$", text, re.M), setting
    assert not re.search(r"^\s*(bot|bot_user):", text, re.M), "no bot user or bot scopes"
    name = re.search(r"^\s+name: (.+)$", text, re.M).group(1)
    description = re.search(r"^\s+description: (.+)$", text, re.M).group(1)
    assert len(name) <= 35 and len(description) <= 140


# ---------------------------------------------------------------- `brain` commands in docs


def code_segments(text: str):
    """Inline code spans and the lines of shell-ish fenced blocks."""
    segments, prose, fence = [], [], None
    for line in text.splitlines():
        marker = re.match(r"^\s*```(\S*)", line)
        if marker:
            fence = None if fence is not None else marker.group(1)
            prose.append("")
            continue
        if fence is None:
            prose.append(line)
        elif fence in SHELL_FENCES:
            segments.append(line)
    segments.extend(re.findall(r"`([^`\n]+)`", "\n".join(prose)))
    return segments


def brain_commands(segment: str):
    """Token lists for each simple command in `segment` that starts with `brain`."""
    for part in re.split(r"&&|\|\||[;|\n]", segment):
        tokens = part.split()
        if tokens and tokens[0] == "$":
            tokens = tokens[1:]
        if len(tokens) >= 2 and tokens[0] == "brain":
            yield tokens


def check_brain_command(tokens):
    command, rest = tokens[1], tokens[2:]
    if command.startswith("-"):
        return None  # `brain --version`, `brain --help`
    if command not in CLI:
        return f"unknown command `brain {command}`"
    subs = CLI[command]
    sub = None
    if None not in subs:
        if not rest or rest[0].startswith("<"):
            return None  # `brain schedule <action>` style placeholder
        sub, rest = rest[0], rest[1:]
        if sub not in subs:
            return f"unknown subcommand `brain {command} {sub}`"
    flags = subs[sub]
    for index, token in enumerate(rest):
        if token.startswith("--"):
            flag = token.split("=", 1)[0]
            if flag not in flags:
                return f"unknown flag `{flag}` for `brain {command}{' ' + sub if sub else ''}`"
            if flag == "--job" and index + 1 < len(rest) and rest[index + 1] not in JOBS:
                return f"unknown job `{rest[index + 1]}`"
    if command == "secret" and sub in ("set", "check", "delete") and rest:
        name = rest[0]
        if not name.startswith("<") and name not in SECRET_NAMES:
            return f"unknown secret name `{name}`"
    return None


@pytest.mark.parametrize("path", kit_markdown(), ids=lambda p: str(p.relative_to(KIT)))
def test_brain_commands_in_kit_docs_exist(path):
    problems = []
    for segment in code_segments(path.read_text(encoding="utf-8")):
        for tokens in brain_commands(segment):
            problem = check_brain_command(tokens)
            if problem:
                problems.append(f"{problem}: {' '.join(tokens)}")
    assert not problems, "\n".join(problems)


def test_brain_command_checker_catches_mistakes():
    assert check_brain_command("brain doctor --json".split()) is None
    assert check_brain_command("brain secret set slack --gui".split()) is None
    assert check_brain_command("brain catchup --job weekly_review".split()) is None
    assert check_brain_command("brain install".split())
    assert check_brain_command("brain slack post hi".split())
    assert check_brain_command("brain update --force".split())
    assert check_brain_command("brain secret set github --gui".split())
    assert check_brain_command("brain catchup --job weekly".split())
    assert list(brain_commands("cd ~/brain && claude")) == []


# ---------------------------------------------------------------- templates and paths


@pytest.mark.parametrize("name", ["me.md", "strategy.md", "people.md", "voice.md"])
def test_templates_have_placeholders(name):
    text = (TEMPLATES / name).read_text(encoding="utf-8")
    assert "{{" in text and text.count("{{") == text.count("}}"), name


def test_template_folders_exist():
    for rel in ("tasks/active/.keep", "tasks/later/.keep", "tasks/archive/.keep", "inbox/.keep"):
        assert (TEMPLATES / rel).is_file(), rel


def text_files(*roots):
    for root in roots:
        for path in sorted(root.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
                yield path


HOME_PATH = re.compile(r"/home/[A-Za-z0-9._-]+")
# Placeholders such as /Users/<user>, /Users/you or a regex like /Users/[^/]+ are fine.
USERS_PATH = re.compile(r"/Users/(?![<\[$({]|you\b|USER\b|NAME\b|name\b|Shared\b)[A-Za-z0-9._-]+")


def test_no_real_home_paths_in_shipped_files():
    problems = []
    for path in text_files(KIT, TEMPLATES):
        text = path.read_text(encoding="utf-8", errors="replace")
        for pattern in (HOME_PATH, USERS_PATH):
            for match in pattern.finditer(text):
                problems.append(f"{path.relative_to(REPO)}: {match.group(0)}")
    assert not problems, "\n".join(problems)


def test_no_research_markers_in_user_facing_copy():
    for path in text_files(KIT, TEMPLATES):
        if path.suffix not in (".md", ".yaml", ".json", ".rules"):
            continue
        text = path.read_text(encoding="utf-8")
        assert "UNCONFIRMED" not in text and "check on the dry run" not in text, path


def test_doc_references_in_kit_exist():
    missing = []
    for path in kit_markdown():
        text = path.read_text(encoding="utf-8")
        for rel in re.findall(r"`((?:docs/[\w.-]+\.md)|tasks/TEMPLATE\.md)`", text):
            if not (KIT / rel).is_file():
                missing.append(f"{path.relative_to(KIT)} -> {rel}")
    assert not missing, missing


def test_setup_prompts_end_every_run():
    text = (KIT / "docs" / "setup-prompts.md").read_text(encoding="utf-8")
    blocks = re.findall(r"```text\n(.*?)```", text, re.S)
    prompts = [b for b in blocks if "Steps:" in b]
    assert len(prompts) >= 5, "prompts A, B, the Google admin fallback, C and D"
    for block in prompts:
        assert "This ends your run." in block
        assert "your turn" in block
    assert "done with Google" in text and "done with Slack" in text
