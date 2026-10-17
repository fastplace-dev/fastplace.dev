#!/usr/bin/env bash
#
# Fastplace one-line installer — macOS and Linux.
#
#   curl -fsSL https://fastplace.dev/install.sh | bash
#
# What it does (and everything it does):
#   1. Installs uv (the Python package manager) with uv's official
#      standalone installer, unless uv is already on PATH.
#   2. Installs the fastplace CLI into its own isolated environment:
#      uv tool install --upgrade --python 3.12 fastplace
#      (uv downloads a managed CPython 3.12; the system Python is never
#      touched). Everything lands under ~/.local — no sudo anywhere.
#   3. Makes sure ~/.local/bin is on your PATH (appends one line to your
#      shell rc file, marked "# fastplace-installer").
#   4. Verifies the install and prints your next step.
#
# Options: --mcp (install the AI-agent extra), --no-modify-path,
# FASTPLACE_VERSION=<x> to pin a release. No other network fetches
# happen beyond uv's installer and PyPI.
#
# Source: https://github.com/fastplace-dev/fastplace.dev/blob/master/installer/install.sh

# Re-exec under bash when piped to `sh` (the docs say `| bash`; be kind
# to people who type `| sh` anyway).
if [ -z "${BASH_VERSION:-}" ]; then
  exec bash "$0" "$@"
fi

set -euo pipefail

INSTALLER_VERSION="1.0.0"
UV_INSTALL_URL="https://astral.sh/uv/install.sh"
UV_TOOL_BIN_DIR="$HOME/.local/bin"
PATH_MARKER="# fastplace-installer"

say() { printf '%s\n' "$*"; }
err() { printf 'error: %s\n' "$*" >&2; }

if [ -t 1 ]; then
  BOLD=$'\033[1m'; DIM=$'\033[2m'; RESET=$'\033[0m'
else
  BOLD=""; DIM=""; RESET=""
fi

# --- platform guard -------------------------------------------------------

os=$(uname -s)
arch=$(uname -m)
case "$os/$arch" in
  Darwin/arm64 | Darwin/x86_64 | Linux/arm64 | Linux/x86_64) ;;
  *)
    err "Unsupported platform: $os on $arch. Install manually with:"
    err "  uv tool install --python 3.12 fastplace"
    exit 2
    ;;
esac

# --- session PATH: make ~/.local/bin visible right now --------------------

export PATH="$UV_TOOL_BIN_DIR:$PATH"

# --- options ----------------------------------------------------------------

# The package spec grows here: --mcp swaps in the AI-agent extra, and a
# valid FASTPLACE_VERSION pins a release. Validation happens before any
# network call so a typo can never leave a half-configured machine.
install_spec="fastplace"
while [ $# -gt 0 ]; do
  case "$1" in
    --mcp)
      install_spec="fastplace[mcp]"
      ;;
    *)
      err "Unknown option: $1"
      exit 2
      ;;
  esac
  shift
done

if [ -n "${FASTPLACE_VERSION:-}" ]; then
  if ! [[ "$FASTPLACE_VERSION" =~ ^[0-9]+(\.[0-9]+){0,2}$ ]]; then
    err "FASTPLACE_VERSION must be a release like 0.4.3, got: $FASTPLACE_VERSION"
    exit 2
  fi
  install_spec+="==$FASTPLACE_VERSION"
fi

# --- 1. uv ----------------------------------------------------------------

say "${DIM}fastplace installer $INSTALLER_VERSION${RESET}"

if ! command -v uv >/dev/null 2>&1; then
  say "${DIM}Installing uv (https://astral.sh)…${RESET}"
  if ! curl -fsSL "$UV_INSTALL_URL" | bash; then
    err "Failed to install uv. Check your connection and retry, or install uv"
    err "from https://docs.astral.sh/uv/ and re-run this script."
    exit 1
  fi
  hash -r
fi

if ! command -v uv >/dev/null 2>&1; then
  err "uv was installed but is not on PATH. Open a new terminal and re-run,"
  err "or see https://docs.astral.sh/uv/getting-started/installation/"
  exit 1
fi

# --- 2. fastplace CLI -----------------------------------------------------

say "${DIM}Installing $install_spec (managed Python 3.12, isolated environment)…${RESET}"
uv tool install --upgrade --python 3.12 "$install_spec"

# --- 3. persistent PATH ---------------------------------------------------

rc_file="$HOME/.bashrc"
case "${SHELL:-}" in
  */zsh) rc_file="$HOME/.zshrc" ;;
esac

path_modified=0
case ":$PATH:" in
  *":$UV_TOOL_BIN_DIR:"*) ;;
  *) ;;
esac
# PATH exported above covers this session; persist for future shells.
needs_persist=1
if [ -f "$rc_file" ] && grep -qF "$PATH_MARKER" "$rc_file" 2>/dev/null; then
  needs_persist=0
fi
if [ "$needs_persist" = 1 ]; then
  {
    printf '\n%s\n' "$PATH_MARKER"
    # shellcheck disable=SC2016  # $HOME must land literally in the rc file
    printf 'export PATH="$HOME/.local/bin:$PATH"\n'
  } >>"$rc_file" 2>/dev/null || true
  if grep -qF "$PATH_MARKER" "$rc_file" 2>/dev/null; then
    path_modified=1
  fi
fi

# --- 4. verify ------------------------------------------------------------

FASTPLACE_BIN="$UV_TOOL_BIN_DIR/fastplace"
if [ ! -x "$FASTPLACE_BIN" ]; then
  err "fastplace was installed but $FASTPLACE_BIN is missing."
  err "Run 'uv tool install --python 3.12 fastplace' manually to inspect."
  exit 1
fi
installed_version=$("$FASTPLACE_BIN" --version)

# --- 5. next steps --------------------------------------------------------

say ""
say "${BOLD}Fastplace is ready.${RESET}"
say "  $installed_version"
say ""
say "Next step:"
say "  ${BOLD}fastplace new my-app${RESET}"
if [ "$path_modified" = 1 ]; then
  say ""
  say "${DIM}PATH updated in $(basename "$rc_file") — open a new terminal for it to"
  say "take effect.${RESET}"
fi
say ""
say "${DIM}Docs: https://fastplace.dev — uninstall: rm -f ~/.local/bin/fastplace"
say "&& uv tool uninstall fastplace${RESET}"
