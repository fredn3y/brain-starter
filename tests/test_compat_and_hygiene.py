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
    "2d8d3f3a0f06b516",
    "3bf8fd1a8fc44d4a",
    "648ce05a806ea165",
    "6827ed4185621fca",
    "720ae25dcde37ed8",
    "7282f5e17dc8425d",
    "9054966f3df0a88c",
    "9b7a0f2e159a5d55",
    "a5ca3725d59b501f",
    "ab1cb712f2dca756",
    "ac9071cf9ea41bfb",
    "adb1b615b6e92302",
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


def forbidden_lines(text: str, hashes=FORBIDDEN_HASHES):
    """Line numbers holding a forbidden word. Matching ignores case; a word counts on its own, as
    a piece of a hyphenated word (`name-shop`), inside an address (`x.name.com`) and as a pair of
    neighbouring words (for two-word names)."""
    for allowed in ALLOWED:
        text = text.replace(allowed, "")
    hits = []
    for number, line in enumerate(text.splitlines(), 1):
        words = re.findall(r"[a-z0-9][a-z0-9-]*", line.lower())
        candidates = set(words) | {f"{a} {b}" for a, b in zip(words, words[1:])}
        candidates |= {part for word in words for part in word.split("-") if part}
        if any(_hash(word) in hashes for word in candidates):
            hits.append(number)
    return hits


def test_no_personal_or_client_strings():
    problems = []
    for path in shipped_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        for number in forbidden_lines(text):
            problems.append(f"{path.relative_to(REPO)}:{number}: forbidden word")
    assert not problems, "personal/client strings in shipped files:\n" + "\n".join(sorted(set(problems)))


def test_the_guard_catches_a_planted_word_in_every_form():
    # An invented word stands in for a real one, so this public test names nobody.
    hashes = {_hash("zorvane"), _hash("quill-ten-07"), _hash("ada brook")}
    caught = ["Zorvane said yes", "ZORVANE's shop", "see zorvane-studio", "mail me@zorvane.co.uk",
              "(zorvane)", "zorvane_notes", "host quill-ten-07.example.net", "ask Ada Brook today",
              "https://example.com/zorvane/plan"]
    for line in caught:
        assert forbidden_lines(line, hashes) == [1], line
    for line in ["zorvanes", "azorvane", "quill-ten", "Ada Brookes", "a brook"]:
        assert forbidden_lines(line, hashes) == [], line
    assert forbidden_lines("one\ntwo zorvane\n", hashes) == [2]


def test_the_allowed_repo_slug_passes_but_its_owner_word_alone_does_not():
    owner, _, repo = ALLOWED[0].partition("/")
    hashes = {_hash(owner)}
    assert forbidden_lines(f"https://github.com/{ALLOWED[0]}/releases", hashes) == []
    assert forbidden_lines(f"written by {owner}", hashes) == [1]
