#!/bin/bash
# Installs (or refreshes) the brain kit in ~/brain:
#
#   curl -fsSL https://github.com/fredn3y/brain-starter/releases/latest/download/install.sh | bash
#
# Safe to run again: your own files are never overwritten.
# It installs Claude Code, T3 Code and the brain folder. Google (gws), Codex, Slack and the daily
# schedule are add-ons, turned on later by asking in a chat.
# For the consultant: BRAIN_HOME, BRAIN_KIT_VERSION (e.g. v0.2.0), BRAIN_KIT_REPO,
# BRAIN_KIT_TARBALL + BRAIN_KIT_SUMS (local path or URL), BRAIN_SKIP_TOOLS=1,
# BRAIN_ALLOW_NON_MAC=1, BRAIN_PYTHON, and BRAIN_EXTRA_TOOLS to add tools in the same run
# (names from tools.json, comma or space separated), for the full setup:
#
#   curl -fsSL https://github.com/fredn3y/brain-starter/releases/latest/download/install.sh | BRAIN_EXTRA_TOOLS=gws,codex bash
#
# Written for macOS /bin/bash 3.2. It arrives through a pipe, so it never reads from stdin, and
# everything runs from main() at the very end: a cut-off download runs nothing.

set -u

BRAIN_TMP=""
PY=""
REL=""
TARBALL_NAME=""

say() { printf '%s\n' "$*"; }
step() { printf '\n==> %s\n' "$*"; }

fail() {
  printf '\nSorry, the install stopped: %s\n' "$*" >&2
  printf 'Please copy everything in this window and send it to your consultant.\n' >&2
  exit 1
}

cleanup() {
  if [ -n "$BRAIN_TMP" ] && [ -d "$BRAIN_TMP" ]; then
    rm -rf "$BRAIN_TMP"
  fi
}

fetch() {  # fetch <url or local path> <destination file>
  case "$1" in
    https://*|http://*) curl -fsSL --retry 2 -o "$2" "$1" </dev/null ;;
    file://*) cp "${1#file://}" "$2" ;;
    *) cp "$1" "$2" ;;
  esac
}

sha256_of() {
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{ print $1 }'
  elif command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{ print $1 }'
  else
    return 1
  fi
}

check_mac() {
  local os version major
  if [ "$(id -u)" = "0" ]; then
    fail "please run the install line without sudo."
  fi
  os="$(uname -s 2>/dev/null || echo unknown)"
  if [ "$os" != "Darwin" ]; then
    if [ "${BRAIN_ALLOW_NON_MAC:-0}" = "1" ]; then
      say "Not a Mac ($os); carrying on because BRAIN_ALLOW_NON_MAC=1."
      return 0
    fi
    fail "this installer is for a Mac (this computer says: $os)."
  fi
  version="$(sw_vers -productVersion 2>/dev/null || true)"
  major="${version%%.*}"
  case "$major" in
    ''|*[!0-9]*) fail "couldn't read the macOS version." ;;
  esac
  if [ "$major" -lt 13 ]; then
    fail "macOS 13 (Ventura) or newer is needed; this Mac has $version. Update macOS first."
  fi
  if ! xcode-select -p >/dev/null 2>&1; then
    say "Your Mac needs Apple's free Command Line Tools first."
    xcode-select --install >/dev/null 2>&1 </dev/null || true
    say "Click Install in the window that opened. When it finishes, paste the same install line again."
    exit 0
  fi
}

find_python() {
  local candidate
  for candidate in "${BRAIN_PYTHON:-}" /usr/bin/python3 "$(command -v python3 2>/dev/null || true)"; do
    if [ -n "$candidate" ] && [ -x "$candidate" ] &&
      "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' \
        </dev/null >/dev/null 2>&1; then
      PY="$candidate"
      return 0
    fi
  done
  return 1
}

resolve_release() {
  local repo tag json base
  if [ -n "${BRAIN_KIT_TARBALL:-}" ]; then
    TARBALL_NAME="$(basename "$BRAIN_KIT_TARBALL")"
    step "Getting the brain kit ($TARBALL_NAME)"
    fetch "$BRAIN_KIT_TARBALL" "$BRAIN_TMP/$TARBALL_NAME" || fail "couldn't get $BRAIN_KIT_TARBALL."
    if [ -n "${BRAIN_KIT_SUMS:-}" ]; then
      fetch "$BRAIN_KIT_SUMS" "$BRAIN_TMP/SHA256SUMS" || fail "couldn't get $BRAIN_KIT_SUMS."
    else
      say "Warning: BRAIN_KIT_SUMS isn't set, so the download can't be checked."
    fi
    return 0
  fi
  repo="${BRAIN_KIT_REPO:-fredn3y/brain-starter}"
  tag="${BRAIN_KIT_VERSION:-}"
  if [ -z "$tag" ]; then
    step "Finding the newest brain kit"
    json="$(curl -fsSL -H 'Accept: application/vnd.github+json' \
      "https://api.github.com/repos/$repo/releases/latest" </dev/null)" ||
      fail "couldn't reach GitHub. Check the internet connection and try again."
    tag="$(printf '%s' "$json" |
      "$PY" -c 'import json, sys; print(json.load(sys.stdin).get("tag_name") or "")' 2>/dev/null)" ||
      tag=""
    [ -n "$tag" ] || fail "GitHub didn't say which version is the newest. Try again in a few minutes."
  fi
  case "$tag" in
    v*) ;;
    *) tag="v$tag" ;;
  esac
  if ! printf '%s' "$tag" | grep -Eq '^v[0-9]+\.[0-9]+\.[0-9]+$'; then
    fail "couldn't work out which version to install (got '$tag')."
  fi
  TARBALL_NAME="brain-starter-$tag.tar.gz"
  base="https://github.com/$repo/releases/download/$tag"
  step "Downloading the brain kit $tag"
  fetch "$base/$TARBALL_NAME" "$BRAIN_TMP/$TARBALL_NAME" || fail "couldn't download $TARBALL_NAME."
  fetch "$base/SHA256SUMS" "$BRAIN_TMP/SHA256SUMS" || fail "couldn't download the checksum list."
}

verify_download() {
  local expected actual
  if [ ! -f "$BRAIN_TMP/SHA256SUMS" ]; then
    return 0
  fi
  expected="$(awk -v f="$TARBALL_NAME" '$2 == f || $2 == "*" f { print $1; exit }' \
    "$BRAIN_TMP/SHA256SUMS")"
  [ -n "$expected" ] || fail "the checksum list has no entry for $TARBALL_NAME."
  actual="$(sha256_of "$BRAIN_TMP/$TARBALL_NAME")" || fail "no checksum tool (shasum) was found."
  if [ "$expected" != "$actual" ]; then
    fail "the download is damaged (checksum mismatch). Try again in a few minutes."
  fi
}

unpack() {
  local dir
  mkdir "$BRAIN_TMP/release" || fail "couldn't make a temporary folder."
  tar -xzf "$BRAIN_TMP/$TARBALL_NAME" -C "$BRAIN_TMP/release" </dev/null ||
    fail "couldn't unpack the download."
  for dir in "$BRAIN_TMP"/release/brain-starter-v*; do
    if [ -f "$dir/kit/.kit/bin/brain" ]; then
      REL="$dir"
      return 0
    fi
  done
  fail "the download doesn't look like a brain kit."
}

run_brain_install() {
  local status
  set -- install --release-dir "$REL" --workspace "${BRAIN_HOME:-$HOME/brain}"
  if [ "${BRAIN_SKIP_TOOLS:-0}" = "1" ]; then
    set -- "$@" --no-tools
  fi
  if [ -n "${BRAIN_EXTRA_TOOLS:-}" ]; then
    set -- "$@" --with "$BRAIN_EXTRA_TOOLS"
  fi
  step "Setting up your brain"
  "$PY" "$REL/kit/.kit/bin/brain" "$@" </dev/null
  status=$?
  if [ "$status" -eq 75 ]; then  # busy with a scheduled task: nothing changed, just retry
    say "Your brain is busy with a scheduled task. Paste the same install line again in a few minutes."
    exit 1
  fi
  if [ "$status" -eq 3 ]; then  # the brain folder is in place, but Claude Code didn't install
    fail "Claude Code didn't install (see the messages above). Your brain folder is in place. Check the internet connection, then paste the same install line again: it is safe to re-run."
  fi
  if [ "$status" -ne 0 ]; then
    fail "the setup step didn't finish (see the messages above)."
  fi
}

main() {
  say "Installing your brain kit."
  check_mac
  find_python || fail "Python 3.9 or newer is needed (it comes with Apple's Command Line Tools)."
  BRAIN_TMP="$(mktemp -d "${TMPDIR:-/tmp}/brain-install.XXXXXX")" ||
    fail "couldn't make a temporary folder."
  trap cleanup EXIT
  trap 'exit 130' INT TERM
  resolve_release
  verify_download
  unpack
  run_brain_install
}

main
