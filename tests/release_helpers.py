"""Helpers for the release/apply/update tests: synthetic source trees, builds, a fake network.

No real network: `FakeNet` answers brainkit.http's curl calls from local files and canned JSON.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import os
import re
import shutil
from pathlib import Path
from typing import Dict, Optional, Tuple, Union

from brainkit.runner import FakeRunner, Result
from brainkit.safe_tar import safe_extract

REPO = Path(__file__).resolve().parents[1]
DUMMY_BRAIN = "#!/usr/bin/env python3\nprint('placeholder brain')\n"
GENERATED = (".kit/snapshots", ".kit/cache", ".kit/run.lock", "logs", "reports")
_BUILD = None


def build_module():
    global _BUILD
    if _BUILD is None:
        spec = importlib.util.spec_from_file_location(
            "build_release", str(REPO / "tools" / "build_release.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # type: ignore[union-attr]
        _BUILD = module
    return _BUILD


def write(path: Path, content: Union[str, bytes], mode: Optional[int] = None) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content.encode("utf-8") if isinstance(content, str) else content)
    if mode is not None:
        os.chmod(str(path), mode)
    return path


def make_source(root: Path, version: str, files: Optional[Dict[str, Union[str, bytes]]] = None,
                templates: Optional[Dict[str, str]] = None,
                migrations: Optional[Dict[str, str]] = None,
                brain: str = DUMMY_BRAIN) -> Path:
    """A repo-shaped tree: kit/, templates/, migrations/, install.sh."""
    root = Path(root)
    write(root / "kit" / ".kit" / "VERSION", version + "\n")
    write(root / "kit" / ".kit" / "bin" / "brain", brain, mode=0o755)
    for rel, content in (files or {}).items():
        write(root / "kit" / rel, content)
    for rel, content in (templates or {}).items():
        write(root / "templates" / rel, content)
    (root / "migrations").mkdir(parents=True, exist_ok=True)
    for name, body in (migrations or {}).items():
        write(root / "migrations" / name, body)
    write(root / "install.sh", "#!/bin/bash\necho placeholder installer\n", mode=0o755)
    return root


def make_release_dir(root: Path, version: str, **kwargs) -> Path:
    """An unpacked release (same layout as the tarball's top folder) without building a tarball."""
    from brainkit import manifest as manifest_mod

    src = make_source(root, version, **kwargs)
    (src / "manifest.json").write_text(manifest_mod.dumps(manifest_mod.build_manifest(src)))
    return src


def build(root: Path, out: Path) -> Dict[str, Path]:
    return build_module().build(Path(root), Path(out))


def extract_release(tarball: Path, dest: Path) -> Path:
    safe_extract(tarball, dest)
    tops = [p for p in Path(dest).iterdir() if p.is_dir()]
    assert len(tops) == 1, tops
    return tops[0]


def tree(root: Path, skip=GENERATED) -> Dict[str, object]:
    """Everything under root: {rel: (bytes, mode) | ('link', target) | 'dir'}."""
    root = Path(root)
    out: Dict[str, object] = {}
    for dirpath, dirnames, filenames in os.walk(str(root)):
        rel_dir = os.path.relpath(dirpath, str(root)).replace(os.sep, "/")
        rel_dir = "" if rel_dir == "." else rel_dir
        keep = []
        for name in sorted(dirnames):
            rel = f"{rel_dir}/{name}" if rel_dir else name
            full = os.path.join(dirpath, name)
            if rel in skip or name == "__pycache__":
                continue
            if os.path.islink(full):
                out[rel] = ("link", os.readlink(full))
            else:
                out[rel] = "dir"
                keep.append(name)
        dirnames[:] = keep
        for name in filenames:
            rel = f"{rel_dir}/{name}" if rel_dir else name
            full = os.path.join(dirpath, name)
            if rel in skip:
                continue
            if os.path.islink(full):
                out[rel] = ("link", os.readlink(full))
            else:
                out[rel] = (Path(full).read_bytes(), os.stat(full).st_mode & 0o777)
    return out


def github_release(version: str, base: str = "https://example.invalid/dl",
                   notes: str = "What's new") -> dict:
    return {
        "tag_name": f"v{version}",
        "html_url": f"https://github.com/fredn3y/brain-starter/releases/tag/v{version}",
        "body": notes,
        "assets": [
            {"name": f"brain-starter-v{version}.tar.gz",
             "browser_download_url": f"{base}/v{version}/brain-starter-v{version}.tar.gz"},
            {"name": "SHA256SUMS", "browser_download_url": f"{base}/v{version}/SHA256SUMS"},
            {"name": "install.sh", "browser_download_url": f"{base}/v{version}/install.sh"},
        ],
    }


class FakeNet:
    """Answers `curl` from brainkit.http: downloads from local files, API calls from canned JSON."""

    def __init__(self, fake: FakeRunner):
        self.fake = fake
        self.files: Dict[str, Path] = {}
        self.api: Dict[str, Tuple[int, object]] = {}
        fake.on("curl", result=self.respond)

    def serve_release(self, version: str, dist: Dict[str, Path],
                      base: str = "https://example.invalid/dl") -> None:
        self.files[f"{base}/v{version}/brain-starter-v{version}.tar.gz"] = dist["tarball"]
        self.files[f"{base}/v{version}/SHA256SUMS"] = dist["sums"]

    def downloads(self):
        return [c["argv"][-1] for c in self.fake.calls if c["argv"][0] == "curl" and "-o" in c["argv"]]

    def respond(self, argv):
        if "-o" in argv:
            dest, url = argv[argv.index("-o") + 1], argv[-1]
            src = self.files.get(url)
            if src is None:
                return Result(22, "", f"curl: (22) The requested URL returned error: 404 {url}")
            shutil.copyfile(str(src), dest)
            return Result(0)
        if "--config" in argv:
            config = self.fake.calls[-1]["input"] or ""
            match = re.search(r'^url = "(.*)"$', config, re.M)
            url = match.group(1).replace('\\"', '"').replace("\\\\", "\\") if match else ""
            status, body = self.api.get(url, (404, {"message": "Not Found"}))
            header_path = argv[argv.index("--dump-header") + 1]
            Path(header_path).write_text(f"HTTP/2 {status}\ncontent-type: application/json\n\n")
            text = body if isinstance(body, str) else json.dumps(body)
            return Result(0, f"{text}\n{status}")
        return Result(2, "", "unexpected curl call")


def run_apply_in_process(argv) -> Result:
    """Stand-in for `python <release>/kit/.kit/bin/brain _apply ...`: same parser, same code."""
    from brainkit import apply as apply_mod

    parser = argparse.ArgumentParser()
    apply_mod.register(parser.add_subparsers())
    args = parser.parse_args(list(argv[argv.index("_apply"):]))
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = args.func(args)
    return Result(code, out.getvalue(), "")
