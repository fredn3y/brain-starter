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
    "bash -lc 'gws gmail +send --to x@y.z'",  # bundled shell flags, as Codex writes them
]
# Destructive commands: recursive deletes in every spelling, admin rights, killing programs,
# history-wiping git commands. Never run; only fed to the guard as text.
DESTRUCTIVE = [
    "rm -rf ~/brain-test",
    "rm -fr old",
    "rm -Rf old",
    "rm -fR old",
    "rm -r old",
    "rm -R old",
    "rm -r -f old",
    "rm -f -r old",
    "rm -v -i -R old",
    "rm -rfv old",
    "rm -vfr old",
    "rm --recursive old",
    "rm --recursive --force old",
    "rm --rec old",
    "rm old -rf",
    "/bin/rm -rf old",
    "/usr/bin/env rm -rf old",
    "\\rm -rf old",
    "'rm' -rf old",
    "command rm -rf old",
    "rm \\\n-rf old",
    "sh -c 'rm -rf old'",
    "bash -lc \"cd ~ && rm -Rf old\"",
    "eval rm -rf old",
    "bash -lc 'rm -rf old'",
    "zsh -ic 'sudo ls'",
    "x=$(rm -rf old)",
    "(cd ~ && rm -rf old)",
    "find . -name '*.tmp' -exec rm -rf {} +",
    "ls | xargs rm -r",
    "nohup rm -rf old &",
    "cd ~/brain; rm -rf tasks",
    "sudo ls",
    "sudo -s",
    "/usr/bin/sudo rm old",
    "pkill -f node",
    "pkill Finder",
    "killall Finder",
    "/usr/bin/killall -9 Dock",
    "git clean -fdx",
    "git clean -n",
    "git -C ~/brain clean -fd",
    "git reset --hard",
    "git reset --hard HEAD~3",
    "git -c core.pager=cat reset --hard origin/main",
    "git push --force",
    "git push -f",
    "git push -f origin main",
    "git push origin main --force",
    "git push origin main -f",
    "git push -uf origin main",
    "git push --force-with-lease",
    "git push --force-with-lease=main:abc123 origin main",
    "git push origin +main",
    "git -C repo push --force",
    "/usr/bin/git push --force",
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
    # destructive-command rules must leave everyday commands alone
    "rm notes.txt",
    "rm -f notes.txt",
    "rm -i a.txt b.txt",
    "rm -- -r",
    "rmdir empty-folder",
    "brain trash old-folder",
    "brain trash tasks/archive/old.md notes.txt",
    "git status",
    "git push",
    "git push origin main",
    "git push -u origin main",
    "git push --follow-tags",
    "git reset HEAD~1",
    "git reset --soft HEAD~1",
    "git log --grep 'git clean'",
    "git commit -m 'rm -rf old builds is blocked'",
    "man sudo",
    "which pkill",
    "kill 12345",
    "echo 'never run rm -rf'",
    "grep -r rm .",
    "bash -lc 'ls -la'",
]


@pytest.mark.parametrize("command", BLOCKED)
def test_blocks(command):
    assert guard.check_command(command)


@pytest.mark.parametrize("command", DESTRUCTIVE)
def test_blocks_destructive(command):
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


@pytest.mark.parametrize("command", ["rm -rf old", "/bin/rm -r -f old", "sh -c 'rm -Rf old'",
                                     "eval rm -rf old", "git clean -fd"])
def test_delete_blocks_point_to_brain_trash_in_one_line(command):
    message = guard.check_command(command)
    assert message.count("Blocked by your brain's safety rules") == 1
    assert "`brain trash <path>`" in message and "\n" not in message
    assert "\u2014" not in message


def test_nested_shell_messages_are_not_doubled():
    message = guard.check_command("bash -c 'gws gmail +send --to x'")
    assert message.count("Blocked by your brain's safety rules") == 1
    assert message.endswith("for them to paste.")


@pytest.mark.parametrize("command", ["sudo ls", "pkill node", "git reset --hard", "git push -f"])
def test_other_destructive_blocks_say_why_without_trash_hint(command):
    message = guard.check_command(command)
    assert message.startswith("Blocked by your brain's safety rules: ")
    assert "brain trash" not in message


def test_hook_blocks_recursive_delete_with_exit_2(tmp_path):
    code, err = run_hook({"tool_name": "Bash", "tool_input": {"command": "rm -rf ~/trash-test"}}, tmp_path)
    assert code == 2 and "brain trash" in err
    code, _ = run_hook({"tool_name": "Bash", "tool_input": {"command": "rm notes.txt"}}, tmp_path)
    assert code == 0
