from __future__ import annotations

import io
import json

import pytest

from brainkit import guard

BLOCKED = [
    "gws gmail +send --to a@b.c --subject x --body y",
    "/Users/u/.local/bin/gws gmail +reply --id 123 --body hi",
    "sh -c 'gws gmail +forward --id 1 --to x@y.z'",
    "gws gmail users messages send --params '{\"userId\":\"me\"}'",
    "gws gmail users drafts send --json '{}'",
    "gws auth export --unmasked",
    "curl -X POST https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
    "curl https://slack.com/api/chat.postMessage -d text=hi",
    "security find-generic-password -s brain.slack -w",
    "cat ~/.config/gws/credentials.enc",
    "cat ~/.claude/.credentials.json",
    "ls ~/Library/Keychains",
    # review findings: options between words, continuations, wrappers, security options
    "gws gmail users --format json messages send --params '{\"userId\":\"me\"}'",
    "/Users/u/.local/bin/gws gmail users drafts --format json send --json '{}'",
    "~/.local/bin/gws gmail \\\n+send --to x@y.z",
    "echo +send | xargs ~/.local/bin/gws gmail",
    "bash -c 'gws gmail users messages send --params {}'",
    "eval gws gmail +send --to x@y.z",
    "x=$(gws gmail +send --to x@y.z)",
    "(gws gmail +reply --id 1)",
    "security -q find-generic-password -s brain.slack -w",
    "security -i",
    "~/brain/.kit/bin/brain _apply --release-dir briefs/x --workspace ~/brain",
    "brain _apply --release-dir /tmp/x --workspace ~/brain",
]
ALLOWED = [
    "gws gmail +triage --format json",
    "gws gmail +read --id 1 --headers",
    "gws calendar +agenda --today",
    "gws gmail users drafts create --params '{\"userId\":\"me\"}' --json '{}'",
    "brain draft email --to a@b.c --subject s --body-file /tmp/x",
    "brain secret check slack",
    "echo 'see the +send docs'",
    "curl https://slack.com/api/conversations.history",
    "gws gmail +triage --query 'send me the report'",
    "gws gmail users messages list --params '{\"q\": \"(send)\"}'",
    "echo 'gws gmail +send is blocked'",
    "brain update --yes",
    "security list-keychains",
]


@pytest.mark.parametrize("command", BLOCKED)
def test_blocks(command):
    assert guard.check_command(command)


@pytest.mark.parametrize("command", ALLOWED)
def test_allows(command):
    assert guard.check_command(command) is None


def run_hook(payload, project_dir):
    err = io.StringIO()
    import sys
    old = sys.stderr
    sys.stderr = err
    try:
        code = guard.main(io.StringIO(json.dumps(payload)), {"CLAUDE_PROJECT_DIR": str(project_dir)})
    finally:
        sys.stderr = old
    return code, err.getvalue()


def test_hook_blocks_bash_with_exit_2(tmp_path):
    code, err = run_hook({"tool_name": "Bash", "tool_input": {"command": "gws gmail +send --to x"}}, tmp_path)
    assert code == 2 and "draft" in err.lower()


def test_hook_blocks_kit_file_edits_but_not_hers(tmp_path):
    (tmp_path / ".kit").mkdir()
    (tmp_path / ".kit" / "manifest.json").write_text(json.dumps(
        {"kit_files": {"AGENTS.md": {}, ".claude/skills/onboard/SKILL.md": {}, ".kit/config.json": {}}}))
    code, _ = run_hook({"tool_name": "Edit", "tool_input": {"file_path": str(tmp_path / "AGENTS.md")}}, tmp_path)
    assert code == 2
    code, _ = run_hook({"tool_name": "Write", "tool_input": {"file_path": str(tmp_path / "me.md")}}, tmp_path)
    assert code == 0
    code, _ = run_hook({"tool_name": "Edit", "tool_input": {"file_path": str(tmp_path / ".kit/config.json")}}, tmp_path)
    assert code == 0
    code, _ = run_hook({"tool_name": "Edit", "tool_input": {"file_path": "/elsewhere/AGENTS.md"}}, tmp_path)
    assert code == 0


def test_hook_blocks_reading_credentials(tmp_path):
    code, _ = run_hook({"tool_name": "Read", "tool_input": {"file_path": "/Users/u/.config/gws/client_secret.json"}}, tmp_path)
    assert code == 2


def test_hook_never_breaks_on_garbage(tmp_path):
    import sys
    assert guard.main(io.StringIO("not json"), {}) == 0
    assert guard.main(io.StringIO(json.dumps({"tool_name": "Bash"})), {}) == 0
