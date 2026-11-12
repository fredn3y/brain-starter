"""install.sh: bash 3.2 compatible, never reads stdin, and works end to end (macOS faked)."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from brainkit.lock import try_lock
from release_helpers import REPO, build, write

SCRIPT = REPO / "install.sh"
REPO_SLUG = "fredn3y/brain-starter"

# Features newer than macOS's /bin/bash 3.2.
BASH4_ONLY = {
    r"\bdeclare\s+-[a-zA-Z]*[Ag]": "declare -A / declare -g",
    r"\b(mapfile|readarray|coproc)\b": "mapfile / readarray / coproc",
    r"\$\{[^}]*(,,|\^\^|,|\^)\}": "${var,,} case conversion",
    r"\[\[\s*-v\b": "[[ -v ]]",
    r"\|&": "|&",
    r";;?&": ";& / ;;&",
    r"&>>": "&>>",
    r"\b(local|declare|typeset)\s+-n\b": "namerefs",
    r"\$\{[^}]*@[QEPAaKUuLk]\}": "${var@op}",
    r"\bwait\s+-n\b": "wait -n",
    r"\$\{?EPOCH(SECONDS|REALTIME)\b": "$EPOCHSECONDS",
    r"\bshopt\s+-s\s+(globstar|lastpipe|inherit_errexit)\b": "bash 4 shopt",
}

FAKE_CURL = r"""#!/bin/sh
echo "curl $*" >> "$FAKE_LOG"
out=""
url=""
while [ $# -gt 0 ]; do
  case "$1" in
    -o|-H|--retry|--max-time) [ "$1" = "-o" ] && out="$2"; shift 2 ;;
    -*) shift ;;
    *) url="$1"; shift ;;
  esac
done
if [ -z "${FAKE_WEB:-}" ]; then echo "no network in tests" >&2; exit 7; fi
path="$FAKE_WEB/$(printf '%s' "$url" | sed -e 's#^https://##')"
if [ ! -f "$path" ]; then echo "curl: (22) 404 $url" >&2; exit 22; fi
if [ -n "$out" ]; then cp "$path" "$out"; else cat "$path"; fi
"""


@pytest.fixture(scope="module")
def dist(tmp_path_factory):
    return build(REPO, tmp_path_factory.mktemp("dist"))


@pytest.fixture
def mac(tmp_path):
    return make_mac(tmp_path)


def make_mac(tmp_path, clt=True, macos="14.5", os_name="Darwin"):
    """Shims standing in for macOS commands, first on PATH."""
    bin_dir = tmp_path / "fakebin"
    write(bin_dir / "uname", f'#!/bin/sh\nif [ "$1" = "-m" ]; then echo arm64; else echo {os_name}; fi\n',
          0o755)
    write(bin_dir / "sw_vers", f"#!/bin/sh\necho {macos}\n", 0o755)
    probe = ('echo /Library/Developer/CommandLineTools; exit 0' if clt else 'exit 2')
    write(bin_dir / "xcode-select",
          f'#!/bin/sh\necho "xcode-select $*" >> "$FAKE_LOG"\nif [ "$1" = "-p" ]; then {probe}; fi\nexit 0\n',
          0o755)
    write(bin_dir / "curl", FAKE_CURL, 0o755)
    if shutil.which("shasum") is None:
        write(bin_dir / "shasum", '#!/bin/sh\nshift 2\nexec sha256sum "$@"\n', 0o755)
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    (tmp_path / "tmp").mkdir(exist_ok=True)
    return {"bin": bin_dir, "home": home, "log": tmp_path / "fake.log", "tmp": tmp_path / "tmp"}


def run_installer(mac, **extra):
    """`bash < install.sh`, exactly like `curl … | bash` (the script arrives on stdin)."""
    env = {"HOME": str(mac["home"]), "PATH": f"{mac['bin']}:{os.environ.get('PATH', '/usr/bin:/bin')}",
           "FAKE_LOG": str(mac["log"]), "TMPDIR": str(mac["tmp"])}
    env.update({k: str(v) for k, v in extra.items()})
    with open(str(SCRIPT), "rb") as script:
        return subprocess.run(["bash"], stdin=script, env=env, capture_output=True, text=True,
                              timeout=300)


def fake_log(mac):
    return mac["log"].read_text() if mac["log"].exists() else ""


def local_release(dist):
    return {"BRAIN_KIT_TARBALL": dist["tarball"], "BRAIN_KIT_SUMS": dist["sums"],
            "BRAIN_SKIP_TOOLS": "1"}


def test_syntax():
    result = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_bash_32_compatible_and_never_reads_stdin():
    text = SCRIPT.read_text()
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    problems = [label for pattern, label in BASH4_ONLY.items() if re.search(pattern, code)]
    assert not problems, problems
    commands = re.sub(r"'[^']*'", "''", re.sub(r'"(?:\\.|[^"\\])*"', '""', code))  # drop strings
    assert not re.search(r"(^|[;&|({]|\bthen\b|\bdo\b|\belse\b)\s*read\b", commands, re.M), \
        "install.sh must not read stdin (it arrives through a pipe)"
    assert "/dev/tty" not in code and "/dev/stdin" not in code
    lines = [line for line in code.splitlines() if line.strip()]
    assert lines[-1].strip() == "main", "everything must run from main() at the very end"
    assert text.startswith("#!/bin/bash\n")


def test_end_to_end_install_then_rerun(dist, mac):
    result = run_installer(mac, **local_release(dist))
    assert result.returncode == 0, result.stdout + result.stderr
    home = mac["home"]
    brain = home / "brain"
    manifest = json.loads((brain / ".kit" / "manifest.json").read_text())
    for rel in manifest["kit_files"]:
        assert (brain / rel).is_file(), rel
    for rel in manifest["templates"]:
        assert (brain / rel).exists(), rel
    link = home / ".local" / "bin" / "brain"
    assert link.is_symlink()
    assert Path(os.path.realpath(str(link))) == (brain / ".kit" / "bin" / "brain").resolve()
    profile = (home / ".zprofile").read_text()
    assert profile.count("# >>> brain >>>") == 1 and "$HOME/brain/.kit/bin" in profile
    assert "set me up" in result.stdout
    assert "curl" not in fake_log(mac), "a local release must not touch the network"
    assert list(mac["tmp"].iterdir()) == [], "the temp folder must be cleaned up"
    version = subprocess.run([str(link), "--version"], capture_output=True, text=True, timeout=60,
                             env={"HOME": str(home), "PATH": os.environ.get("PATH", "")})
    assert manifest["version"] in version.stdout + version.stderr

    (brain / ".kit" / "config.json").write_text('{"brief_time": "08:15"}\n')
    write(brain / "tasks" / "active" / "plan.md", "# Plan\n")
    with try_lock(brain / ".kit" / "run.lock") as held:  # a catch-up is running right now
        assert held
        busy = run_installer(mac, **local_release(dist))
    assert busy.returncode == 1 and "Paste the same install line again in a few minutes" in busy.stdout
    assert "consultant" not in busy.stderr
    again = run_installer(mac, **local_release(dist))
    assert again.returncode == 0, again.stdout + again.stderr
    assert (brain / ".kit" / "config.json").read_text() == '{"brief_time": "08:15"}\n'
    assert (brain / "tasks" / "active" / "plan.md").read_text() == "# Plan\n"
    assert (home / ".zprofile").read_text() == profile
    assert not list(brain.rglob("*.new"))


def test_default_path_finds_the_latest_release_on_github(dist, mac, tmp_path):
    version = json.loads(subprocess.run(
        ["tar", "-xzOf", str(dist["tarball"]), f"{dist['tarball'].name[:-7]}/manifest.json"],
        capture_output=True, text=True, check=True).stdout)["version"]
    web = tmp_path / "web"
    write(web / "api.github.com" / "repos" / REPO_SLUG / "releases" / "latest",
          json.dumps({"tag_name": f"v{version}"}))
    downloads = web / "github.com" / REPO_SLUG / "releases" / "download" / f"v{version}"
    write(downloads / dist["tarball"].name, dist["tarball"].read_bytes())
    write(downloads / "SHA256SUMS", dist["sums"].read_bytes())
    result = run_installer(mac, FAKE_WEB=web, BRAIN_SKIP_TOOLS="1")
    assert result.returncode == 0, result.stdout + result.stderr
    log = fake_log(mac)
    assert f"https://api.github.com/repos/{REPO_SLUG}/releases/latest" in log
    assert f"https://github.com/{REPO_SLUG}/releases/download/v{version}/SHA256SUMS" in log
    assert (mac["home"] / "brain" / ".kit" / "manifest.json").is_file()


def test_unreadable_github_answer_stops_with_a_clear_message(mac, tmp_path):
    web = tmp_path / "web"
    write(web / "api.github.com" / "repos" / REPO_SLUG / "releases" / "latest", "<html>rate limited")
    result = run_installer(mac, FAKE_WEB=web, BRAIN_SKIP_TOOLS="1")
    assert result.returncode == 1 and "didn't say which version" in result.stderr
    assert not (mac["home"] / "brain").exists()


def test_pinned_version_uses_its_tag(mac):
    result = run_installer(mac, BRAIN_KIT_VERSION="0.4.2")  # no fake web: the download fails
    assert result.returncode == 1
    assert f"releases/download/v0.4.2/brain-starter-v0.4.2.tar.gz" in fake_log(mac)
    assert "api.github.com" not in fake_log(mac)
    assert "consultant" in result.stderr


def test_missing_command_line_tools(tmp_path, dist):
    mac = make_mac(tmp_path, clt=False)
    result = run_installer(mac, **local_release(dist))
    assert result.returncode == 0, result.stderr
    assert "Click Install in the window that opened. When it finishes, paste the same install line again." in result.stdout
    assert "xcode-select --install" in fake_log(mac)
    assert not (mac["home"] / "brain").exists()


def test_refuses_other_systems_and_old_macos(tmp_path, dist):
    linux = make_mac(tmp_path / "a", os_name="Linux")
    result = run_installer(linux, **local_release(dist))
    assert result.returncode == 1 and "for a Mac" in result.stderr
    assert not (linux["home"] / "brain").exists()
    old = make_mac(tmp_path / "b", macos="12.7.4")
    result = run_installer(old, **local_release(dist))
    assert result.returncode == 1 and "macOS 13" in result.stderr
    assert not (old["home"] / "brain").exists()


def test_non_mac_allowed_with_override(tmp_path, dist):
    linux = make_mac(tmp_path, os_name="Linux")
    result = run_installer(linux, BRAIN_ALLOW_NON_MAC="1", **local_release(dist))
    assert result.returncode == 0, result.stdout + result.stderr
    assert (linux["home"] / "brain" / ".kit" / "manifest.json").is_file()


def test_checksum_mismatch_installs_nothing(dist, mac, tmp_path):
    bad = write(tmp_path / "SHA256SUMS", "0" * 64 + f"  {dist['tarball'].name}\n")
    result = run_installer(mac, BRAIN_KIT_TARBALL=dist["tarball"], BRAIN_KIT_SUMS=bad,
                           BRAIN_SKIP_TOOLS="1")
    assert result.returncode == 1 and "checksum" in result.stderr
    assert not (mac["home"] / "brain").exists()
    assert list(mac["tmp"].iterdir()) == []


def test_claude_failing_is_honest_and_extra_tools_reach_brain_install(dist, mac):
    """No network: Claude Code can't install, so the installer must not say the brain is ready.
    BRAIN_EXTRA_TOOLS asks for the add-ons in the same run."""
    release = local_release(dist)
    del release["BRAIN_SKIP_TOOLS"]
    # Only the fake Mac commands and the system basics: this machine's own claude/gws/codex hidden.
    result = run_installer(mac, PATH=f"{mac['bin']}:/usr/bin:/bin", BRAIN_EXTRA_TOOLS="gws,codex",
                           **release)
    out = result.stdout + result.stderr
    assert result.returncode == 1, out
    assert "claude: FAILED" in result.stdout
    assert "t3: FAILED" in result.stdout  # the standard set: Claude Code and T3 Code
    assert "gws: FAILED" in result.stdout and "codex: FAILED" in result.stdout  # the add-ons
    assert "Claude Code didn't install, so your brain can't be used yet." in result.stdout
    assert "paste the same install line again" in result.stderr
    assert "is ready" not in out
    assert (mac["home"] / "brain" / ".kit" / "manifest.json").is_file()  # the folder is in place
    log = fake_log(mac)
    assert "https://claude.ai/install.sh" in log
    assert "https://github.com/pingdotgg/t3code/releases/latest/download/latest-mac.yml" in log


def test_default_install_fetches_only_claude_and_t3(dist, mac):
    release = local_release(dist)
    del release["BRAIN_SKIP_TOOLS"]
    result = run_installer(mac, PATH=f"{mac['bin']}:/usr/bin:/bin", **release)
    assert result.returncode == 1
    assert "gws" not in result.stdout and "codex" not in result.stdout.lower()
    assert "googleworkspace" not in fake_log(mac) and "chatgpt.com" not in fake_log(mac)
