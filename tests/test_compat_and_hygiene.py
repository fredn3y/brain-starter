"""Cross-cutting guards: Python 3.9 syntax, no 3.10+ APIs, no personal or client strings."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PY_ROOTS = [REPO / "kit" / ".kit" / "lib", REPO / "kit" / ".kit" / "bin", REPO / "migrations", REPO / "tools"]

# APIs newer than Python 3.9 that are easy to reach for by habit.
NEWER_APIS = [
    r"\bdatetime\.UTC\b", r"\bfrom datetime import UTC\b", r"\btomllib\b", r"\bzip\([^)]*strict=",
    r"slots=True", r"kw_only=", r"\bitertools\.pairwise\b", r"\bcontextlib\.chdir\b",
    r"\bStrEnum\b", r"\btyping\.Self\b", r"\bExceptionGroup\b", r"\bhashlib\.file_digest\b",
    r"\.hardlink_to\(", r"\bTaskGroup\b",
]


def python_files():
    for root in PY_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_file() and (path.suffix == ".py" or path.name == "brain"):
                yield path


@pytest.mark.parametrize("path", list(python_files()), ids=lambda p: str(p.relative_to(REPO)))
def test_parses_as_python_39(path):
    source = path.read_text(encoding="utf-8")
    ast.parse(source, filename=str(path), feature_version=(3, 9))


@pytest.mark.parametrize("path", list(python_files()), ids=lambda p: str(p.relative_to(REPO)))
def test_no_newer_stdlib_apis(path):
    source = path.read_text(encoding="utf-8")
    for pattern in NEWER_APIS:
        assert not re.search(pattern, source), f"{path.name}: uses {pattern} (needs Python > 3.9)"


def test_runtime_annotations_safe(tmp_path):
    """Modules using `X | None` in annotations must import annotations from __future__."""
    for path in python_files():
        source = path.read_text(encoding="utf-8")
        if re.search(r":\s*[\w\[\], ]+\s\|\s", source) or re.search(r"->\s*[\w\[\], ]+\s\|\s", source):
            assert "from __future__ import annotations" in source, path


# Words that must never ship in the public template (personal, client and private-host names).
# Stored as truncated SHA-256 hashes so this public test doesn't itself publish the names.
# Add one: python3 -c "import hashlib;print(hashlib.sha256(b'word').hexdigest()[:16])"
FORBIDDEN_HASHES = {
    "0739ecedc8a3e06a",
    "294aa8d75483b833",
    "3bf8fd1a8fc44d4a",
    "6827ed4185621fca",
    "7282f5e17dc8425d",
    "9054966f3df0a88c",
    "9b7a0f2e159a5d55",
    "a5ca3725d59b501f",
    "ab1cb712f2dca756",
    "c0fc092abf25c405",
    "c18d0f640b1ced02",
    "c5e09d80d3e0cb5d",
    "c72d4b733b0760df",
    "d0cfc2e5319b82cd",
    "e28200d81cb7d992",
    "e3272301a91d8543",
    "fc881aa34d44660e",
}
# The kit's own public repo slug is allowed: updates must know where to look.
ALLOWED = ["fredn3y/brain-starter"]
SHIPPED = [REPO / "kit", REPO / "templates", REPO / "migrations", REPO / "install.sh", REPO / "README.md"]
TEXT_SUFFIXES = {".md", ".py", ".json", ".yaml", ".yml", ".toml", ".sh", ".txt", ".plist", ""}


def shipped_files():
    for root in SHIPPED:
        if root.is_file():
            yield root
        elif root.is_dir():
            for path in root.rglob("*"):
                if path.is_file() and path.suffix in TEXT_SUFFIXES and "__pycache__" not in path.parts:
                    yield path


def _hash(word: str) -> str:
    import hashlib
    return hashlib.sha256(word.encode("utf-8")).hexdigest()[:16]


def test_no_personal_or_client_strings():
    problems = []
    for path in shipped_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        for allowed in ALLOWED:
            text = text.replace(allowed, "")
        for number, line in enumerate(text.splitlines(), 1):
            words = re.findall(r"[a-z0-9][a-z0-9-]*", line.lower())
            candidates = set(words) | {f"{a} {b}" for a, b in zip(words, words[1:])}
            for word in candidates:
                if _hash(word) in FORBIDDEN_HASHES:
                    problems.append(f"{path.relative_to(REPO)}:{number}: forbidden word")
    assert not problems, "personal/client strings in shipped files:\n" + "\n".join(sorted(set(problems)))
