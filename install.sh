#!/bin/sh
# shelldeck installer for Linux (and macOS, untested).
#   curl -fsSL https://raw.githubusercontent.com/codejunction/shelldeck/main/install.sh | sh
# Installs uv if missing, then shelldeck as a uv tool (uv fetches Python 3.12+ itself if needed).
# SHELLDECK_SOURCE overrides what gets installed (a wheel path, or git+https://github.com/codejunction/shelldeck).
set -eu

source="${SHELLDECK_SOURCE:-shelldeck}"
bin="$HOME/.local/bin"

if [ -t 1 ]; then c_head='\033[1;35m' c_ok='\033[1;32m' c_off='\033[0m'; else c_head='' c_ok='' c_off=''; fi
say() { printf '  %b%s%b\n' "${2:-}" "$1" "$c_off"; }

echo
say "shelldeck installer" "$c_head"

if ! command -v uv >/dev/null 2>&1; then
  say "installing uv (https://docs.astral.sh/uv) ..."
  if command -v curl >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
  else
    wget -qO- https://astral.sh/uv/install.sh | sh
  fi
  PATH="$bin:$PATH"
fi

say "installing $source ..."
uv tool install --force --python '>=3.12' "$source"
[ -n "${UV_NO_MODIFY_PATH:-}" ] || uv tool update-shell >/dev/null 2>&1 || true

echo
say "shelldeck is installed." "$c_ok"
say "Run  sd  (or  shelldeck) to start it and open your browser."
case ":$PATH:" in *":$bin:"*) ;; *) say "Open a new terminal (or add $bin to PATH) if the command is not found." ;; esac
echo
