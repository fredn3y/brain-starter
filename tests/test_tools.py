from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import plistlib
import re
import shutil
import tarfile
import zipfile
from pathlib import Path

import pytest

from brainkit import tools
from brainkit.cli import main as cli_main
from brainkit.runner import FakeRunner, Result
from brainkit.tools import ToolError, detect_arch, install_many, install_tool, load_tools, tool_status
from release_helpers import FakeNet, write

LOG = logging.getLogger("test-tools")
APP = "T3 Code (Alpha).app"


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    path = tmp_path / "home"
    path.mkdir()
    monkeypatch.setenv("HOME", str(path))
    monkeypatch.delenv("BRAIN_HOME", raising=False)
    return path


def digest(path: Path, algo: str = "sha256") -> str:
    return hashlib.new(algo, Path(path).read_bytes()).hexdigest()


def gws_tarball(tmp_path: Path) -> Path:
    path = tmp_path / "fixtures" / "google-workspace-cli-aarch64-apple-darwin.tar.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(str(path), "w:gz") as archive:
        for name, data, mode in (("google-workspace-cli-aarch64-apple-darwin/gws",
                                  b"#!/bin/sh\necho gws 0.22.5\n", 0o755),
                                 ("google-workspace-cli-aarch64-apple-darwin/README.md", b"hi", 0o644)):
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), mode
            archive.addfile(info, io.BytesIO(data))
    return path


def gws_tool(url: str, sha: str, algo: str = "sha256") -> dict:
    asset = {"url": url, algo: sha}
    return {"name": "gws", "required": True, "check": ["gws", "--version"],
            "install": {"type": "archive-binary", "version": "0.22.5", "binary": "gws",
                        "dest": "~/.local/bin/gws", "assets": {"arm64": asset, "x86_64": asset}}}


def t3_tool(url: str, sha512: str) -> dict:
    asset = {"url": url, "sha512": sha512}
    return {"name": "t3", "required": False, "check": [],
            "install": {"type": "mac-app-zip", "version": "0.0.45", "app": APP,
                        "assets": {"arm64": asset, "x86_64": asset}}}


def arm(fake: FakeRunner) -> FakeRunner:
    return fake.on("uname", "-m", result=Result(0, "arm64\n"))


def test_repo_tools_json(repo):
    entries = {t["name"]: t for t in load_tools(repo / "kit" / ".kit" / "tools.json")}
    assert list(entries) == ["claude", "gws", "codex", "t3"]
    claude, gws, codex, t3 = (entries[n] for n in ("claude", "gws", "codex", "t3"))
    assert claude["required"] and claude["check"] == ["claude", "--version"]
    assert claude["install"] == {"type": "script", "url": "https://claude.ai/install.sh",
                                 "shell": "bash", "args": ["stable"]}
    assert gws["required"] and gws["install"]["type"] == "archive-binary"
    assert gws["install"]["version"] == "0.22.5" and gws["install"]["binary"] == "gws"
    assert gws["install"]["dest"] == "~/.local/bin/gws"
    assert not codex["required"] and codex["install"]["shell"] == "sh"
    assert codex["install"]["url"] == "https://chatgpt.com/codex/install.sh"
    assert not t3["required"] and t3["install"]["app"] == APP
    assert t3["install"]["version"] == "0.0.45"
    for tool, algo, length in ((gws, "sha256", 64), (t3, "sha512", 128)):
        assets = tool["install"]["assets"]
        assert sorted(assets) == ["arm64", "x86_64"]
        for asset in assets.values():
            assert asset["url"].startswith("https://github.com/")
            assert re.match(r"^[0-9a-f]{%d}$" % length, asset[algo])
            assert tool["install"]["version"] in asset["url"]


def test_detect_arch():
    assert detect_arch(FakeRunner().on("uname", "-m", result=Result(0, "arm64\n"))) == "arm64"
    assert detect_arch(FakeRunner().on("uname", "-m", result=Result(0, "aarch64\n"))) == "arm64"
    translated = FakeRunner().on("uname", "-m", result=Result(0, "x86_64\n")).on(
        "sysctl", result=Result(0, "1\n"))
    assert detect_arch(translated) == "arm64"
    assert detect_arch(FakeRunner().on("uname", "-m", result=Result(0, "x86_64\n"))) == "x86_64"


def test_status_installed_missing_and_local_bin_fallback(home):
    claude = {"name": "claude", "required": True, "check": ["claude", "--version"],
              "install": {"type": "script", "url": "https://claude.ai/install.sh"}}
    fake = FakeRunner().on("claude", "--version", result=Result(0, "2.1.0 (Claude Code)\n"))
    status = tool_status(claude, fake, home)
    assert status.installed and status.version == "2.1.0 (Claude Code)" and status.required
    assert not tool_status(claude, FakeRunner(), home).installed
    local = write(home / ".local" / "bin" / "claude", "#!/bin/sh\n", mode=0o755)
    fake = FakeRunner().on(str(local), "--version", result=Result(0, "2.1.0 (Claude Code)\n"))
    status = tool_status(claude, fake, home)
    assert status.installed and status.where == str(local)


def test_status_prefers_the_installed_binary_over_the_path_shim(home):
    dest = write(home / ".local" / "bin" / "gws", "#!/bin/sh\n", mode=0o755)
    fake = FakeRunner().on(str(dest), "--version", result=Result(0, "gws 0.22.5\n"))
    status = tool_status(gws_tool("https://x.invalid/g.tgz", "0" * 64), fake, home)
    assert status.installed and status.version == "gws 0.22.5"
    assert fake.argvs()[0] == [str(dest), "--version"]


def test_status_mac_app_reads_the_bundle_version(home, tmp_path):
    system = tmp_path / "Applications"
    tool = t3_tool("https://x.invalid/t3.zip", "0" * 128)
    assert not tool_status(tool, FakeRunner(), home, apps_dirs=[system, home / "Applications"]).installed
    contents = home / "Applications" / APP / "Contents"
    contents.mkdir(parents=True)
    with open(str(contents / "Info.plist"), "wb") as handle:
        plistlib.dump({"CFBundleShortVersionString": "0.0.45"}, handle)
    status = tool_status(tool, FakeRunner(), home, apps_dirs=[system, home / "Applications"])
    assert status.installed and status.version == "0.0.45"


@pytest.mark.parametrize("entry, command", [
    ({"type": "script", "url": "https://claude.ai/install.sh", "shell": "bash", "args": ["stable"]},
     "curl -fsSL https://claude.ai/install.sh | bash -s stable"),
    ({"type": "script", "url": "https://chatgpt.com/codex/install.sh", "shell": "sh"},
     "curl -fsSL https://chatgpt.com/codex/install.sh | sh"),
], ids=["claude", "codex"])
def test_script_installs_run_the_vendor_installer(home, entry, command):
    state = {"installed": False}

    def run_installer(argv):
        state["installed"] = True
        return Result(0)

    def check(argv):
        return Result(0, "1.0.0\n") if state["installed"] else Result(127, "", "not found")

    fake = FakeRunner().on("bash", "-c", result=run_installer).on("tool", result=check)
    tool = {"name": "tool", "required": True, "check": ["tool", "--version"], "install": entry}
    outcome = install_tool(tool, fake, home, LOG)
    assert (outcome.status, outcome.message) == ("installed", "1.0.0")
    call = next(c for c in fake.calls if c["argv"][0] == "bash")
    assert call["argv"] == ["bash", "-c", "set -o pipefail; " + command]
    assert call["env"]["HOME"] == str(home)


def test_script_install_failure_is_reported(home):
    fake = FakeRunner().on("bash", "-c", result=Result(1, "", "curl: (6) Could not resolve host"))
    tool = {"name": "claude", "required": True, "check": ["claude", "--version"],
            "install": {"type": "script", "url": "https://claude.ai/install.sh"}}
    outcome = install_tool(tool, fake, home, LOG)
    assert outcome.status == "failed" and "Could not resolve host" in outcome.message


def test_archive_binary_install_from_tarball(home, tmp_path):
    fixture = gws_tarball(tmp_path)
    url = "https://example.invalid/v0.22.5/google-workspace-cli-aarch64-apple-darwin.tar.gz"
    fake = arm(FakeRunner())
    FakeNet(fake).files[url] = fixture
    dest = home / ".local" / "bin" / "gws"
    fake.on(str(dest), "--version", result=Result(0, "gws 0.22.5\n"))
    outcome = install_tool(gws_tool(url, digest(fixture)), fake, home, LOG)
    assert (outcome.status, outcome.message) == ("installed", "gws 0.22.5")
    assert dest.read_bytes() == b"#!/bin/sh\necho gws 0.22.5\n"
    assert os.stat(dest).st_mode & 0o777 == 0o755
    assert not any(p.name.startswith(".gws") for p in dest.parent.iterdir())


def test_archive_binary_install_from_zip(home, tmp_path):
    fixture = tmp_path / "gws.zip"
    with zipfile.ZipFile(str(fixture), "w") as archive:
        archive.writestr("pkg/bin/gws", "#!/bin/sh\necho zip\n")
    url = "https://example.invalid/gws.zip"
    fake = arm(FakeRunner())
    FakeNet(fake).files[url] = fixture
    dest = home / ".local" / "bin" / "gws"
    fake.on(str(dest), result=Result(0, "gws 0.22.5\n"))
    assert install_tool(gws_tool(url, digest(fixture)), fake, home, LOG).status == "installed"
    assert dest.read_text() == "#!/bin/sh\necho zip\n" and os.stat(dest).st_mode & 0o777 == 0o755


def test_checksum_mismatch_installs_nothing(home, tmp_path):
    fixture = gws_tarball(tmp_path)
    url = "https://example.invalid/gws.tar.gz"
    fake = arm(FakeRunner())
    FakeNet(fake).files[url] = fixture
    outcome = install_tool(gws_tool(url, "0" * 64), fake, home, LOG)
    assert outcome.status == "failed" and "checksum" in outcome.message
    assert not (home / ".local" / "bin" / "gws").exists()


def test_todo_pins_are_refused_without_downloading(home):
    fake = arm(FakeRunner())
    net = FakeNet(fake)
    outcome = install_tool(gws_tool("TODO", "TODO"), fake, home, LOG)
    assert outcome.status == "not-configured" and "Ask your consultant" in outcome.message
    script = {"name": "x", "required": False, "check": ["x"],
              "install": {"type": "script", "url": "TODO"}}
    assert install_tool(script, fake, home, LOG).status == "not-configured"
    assert net.downloads() == [] and not any(c["argv"][0] == "bash" for c in fake.calls)


def test_non_https_and_bad_hash_are_refused(home):
    fake = arm(FakeRunner())
    assert install_tool(gws_tool("http://example.invalid/g.tgz", "0" * 64), fake, home,
                        LOG).status == "failed"
    assert install_tool(gws_tool("https://example.invalid/g.tgz", "xyz"), fake, home,
                        LOG).status == "failed"


def mac_runner(tmp_path, fixture, url, version="0.0.45"):
    fake = arm(FakeRunner())
    FakeNet(fake).files[url] = fixture

    def ditto(argv):
        if argv[1:3] == ["-x", "-k"]:
            contents = Path(argv[4]) / APP / "Contents"
            (contents / "MacOS").mkdir(parents=True)
            with open(str(contents / "Info.plist"), "wb") as handle:
                plistlib.dump({"CFBundleShortVersionString": version}, handle)
            (contents / "MacOS" / "T3 Code").write_text("app")
            return Result(0)
        shutil.copytree(argv[1], argv[2])
        return Result(0)

    return fake.on("ditto", result=ditto)


def test_mac_app_installs_into_a_writable_applications_folder(home, tmp_path):
    fixture = write(tmp_path / "T3-Code-0.0.45-arm64.zip", b"zip bytes")
    url = "https://example.invalid/T3-Code-0.0.45-arm64.zip"
    system = tmp_path / "Applications"
    system.mkdir()
    fake = mac_runner(tmp_path, fixture, url)
    outcome = install_tool(t3_tool(url, digest(fixture, "sha512")), fake, home, LOG,
                           apps_dirs=[system, home / "Applications"])
    assert (outcome.status, outcome.message) == ("installed", "0.0.45")
    assert (system / APP / "Contents" / "MacOS" / "T3 Code").read_text() == "app"
    assert not (home / "Applications").exists()
    unpack = next(c["argv"] for c in fake.calls if c["argv"][:3] == ["ditto", "-x", "-k"])
    assert unpack[3].endswith("T3-Code-0.0.45-arm64.zip")


def test_mac_app_falls_back_to_home_applications_and_replaces_an_old_copy(home, tmp_path):
    fixture = write(tmp_path / "t3.zip", b"zip bytes")
    url = "https://example.invalid/t3.zip"
    old = home / "Applications" / APP / "Contents"
    old.mkdir(parents=True)
    (old / "stale.txt").write_text("old version")
    fake = mac_runner(tmp_path, fixture, url, version="0.0.46")
    outcome = install_tool(t3_tool(url, digest(fixture, "sha512")), fake, home, LOG, force=True,
                           apps_dirs=[tmp_path / "missing-Applications", home / "Applications"])
    assert (outcome.status, outcome.message) == ("installed", "0.0.46")
    assert not (old / "stale.txt").exists()
    assert sorted(p.name for p in (home / "Applications").iterdir()) == [APP]


def test_mac_app_sha512_mismatch(home, tmp_path):
    fixture = write(tmp_path / "t3.zip", b"zip bytes")
    url = "https://example.invalid/t3.zip"
    fake = mac_runner(tmp_path, fixture, url)
    outcome = install_tool(t3_tool(url, "a" * 128), fake, home, LOG,
                           apps_dirs=[tmp_path / "A", home / "Applications"])
    assert outcome.status == "failed" and "sha512" in outcome.message
    assert not any(c["argv"][0] == "ditto" for c in fake.calls)


def test_manual_instructions(home):
    manual = {"name": "helper", "required": False, "check": ["helper", "--version"],
              "install": {"type": "manual", "instructions": "Open the helper website."}}
    outcome = install_tool(manual, FakeRunner(), home, LOG)
    assert (outcome.status, outcome.message, outcome.ok) == ("manual", "Open the helper website.", True)
    manual["install"]["instructions"] = "TODO"
    assert install_tool(manual, FakeRunner(), home, LOG).status == "not-configured"


def test_present_tools_are_skipped_unless_forced(home):
    tool = {"name": "claude", "required": True, "check": ["claude", "--version"],
            "install": {"type": "script", "url": "https://claude.ai/install.sh"}}
    fake = FakeRunner().on("claude", result=Result(0, "2.1.0\n")).on("bash", result=Result(0))
    assert install_tool(tool, fake, home, LOG).status == "present"
    assert not any(c["argv"][0] == "bash" for c in fake.calls)
    assert install_tool(tool, fake, home, LOG, force=True).status == "installed"
    assert any(c["argv"][0] == "bash" for c in fake.calls)


def test_install_many_selects_by_name_and_rejects_unknown(home):
    tools_list = [{"name": "a", "check": ["a"], "install": {"type": "manual", "instructions": "do a"}},
                  {"name": "b", "check": ["b"], "install": {"type": "manual", "instructions": "do b"}}]
    outcomes = install_many(tools_list, FakeRunner(), home, LOG, names=["b"])
    assert [o.name for o in outcomes] == ["b"]
    with pytest.raises(ToolError, match="unknown"):
        install_many(tools_list, FakeRunner(), home, LOG, names=["zzz"])


def test_load_tools_rejects_bad_files(tmp_path):
    for content in ("not json", json.dumps({"tools": {}}),
                    json.dumps({"tools": [{"name": "x", "install": {"type": "magic"}}]})):
        path = write(tmp_path / "tools.json", content)
        with pytest.raises(ToolError):
            load_tools(path)


def test_cli_status_and_install(ws, monkeypatch, capsys):
    write(ws.tools_file, json.dumps({"tools": [
        {"name": "claude", "required": True, "check": ["claude", "--version"],
         "install": {"type": "script", "url": "https://claude.ai/install.sh"}},
        {"name": "helper", "required": False, "check": ["helper", "--version"],
         "install": {"type": "manual", "instructions": "Do it by hand."}}]}))
    fake = FakeRunner().on("claude", result=Result(0, "2.1.0 (Claude Code)\n"))
    monkeypatch.setattr(tools, "Runner", lambda: fake)
    assert cli_main(["--workspace", str(ws.root), "tools", "status", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert [(r["name"], r["installed"]) for r in rows] == [("claude", True), ("helper", False)]
    assert cli_main(["--workspace", str(ws.root), "tools"]) == 0
    assert "claude" in capsys.readouterr().out
    assert cli_main(["--workspace", str(ws.root), "tools", "install", "helper"]) == 0
    assert "helper: to do by hand (Do it by hand.)" in capsys.readouterr().out
