#!/bin/sh
# shelldeck installer for Linux (and macOS, untested: there it installs the Python package).
#   curl -fsSL https://raw.githubusercontent.com/codejunction/shelldeck/main/install.sh | sh
# Installs the standalone build (no Python needed) from the GitHub release into ~/.local/share/shelldeck:
# versions/<ver>/ + current.txt + the sd shim, with that folder on your PATH. Run it again to update.
#   SHELLDECK_VERSION=0.0.12rc2   a specific release (tag v0.0.12rc2) instead of the latest
#   SHELLDECK_ARCHIVE=<file>      install a local shelldeck-<ver>-linux-x64.tar.gz (its .sha256 next to it is checked)
#   SHELLDECK_ROOT=<dir>          install somewhere else
#   SHELLDECK_NO_MODIFY_PATH=1    leave PATH alone (UV_NO_MODIFY_PATH works too)
#   SHELLDECK_PIP=1 (or --pip)    the Python package instead: uv (installed if missing) + `uv tool install shelldeck`,
#                                 updated with `uv tool upgrade shelldeck`; SHELLDECK_SOURCE (a wheel path, or
#                                 git+https://...) implies it. Piped:  curl -fsSL <url> | sh -s -- --pip
set -eu

repo=codejunction/shelldeck
pip="${SHELLDECK_PIP:-}"
[ "${1:-}" = "--pip" ] && pip=1
[ -n "${SHELLDECK_SOURCE:-}" ] && pip=1

if [ -t 1 ]; then c_head='\033[1;35m' c_ok='\033[1;32m' c_warn='\033[1;33m' c_off='\033[0m'; else c_head='' c_ok='' c_warn='' c_off=''; fi
say() { printf '  %b%s%b\n' "${2:-}" "$1" "$c_off"; }
die() { say "$1" "$c_warn"; exit 1; }
fetch() {  # fetch URL FILE
  if command -v curl >/dev/null 2>&1; then curl -fsSL "$1" -o "$2"; else wget -qO "$2" "$1"; fi
}

echo
say "shelldeck installer" "$c_head"

install_pip() {
  source="${SHELLDECK_SOURCE:-shelldeck}"
  if ! command -v uv >/dev/null 2>&1; then
    say "installing uv (https://docs.astral.sh/uv) ..."
    if command -v curl >/dev/null 2>&1; then
      curl -LsSf https://astral.sh/uv/install.sh | sh
    else
      wget -qO- https://astral.sh/uv/install.sh | sh
    fi
    PATH="$HOME/.local/bin:$PATH"
  fi
  bin="$(uv tool dir --bin)"  # honours UV_TOOL_BIN_DIR
  say "installing $source ..."
  uv tool install --force --upgrade --python '>=3.12' "$source"
  [ -n "${UV_NO_MODIFY_PATH:-}" ] || uv tool update-shell >/dev/null 2>&1 || true
  case ":$PATH:" in *":$bin:"*) ;; *) say "Open a new terminal (or add $bin to PATH) if the command is not found." ;; esac
}

install_standalone() {
  root="${SHELLDECK_ROOT:-${XDG_DATA_HOME:-$HOME/.local/share}/shelldeck}"
  tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp"' EXIT
  if [ -n "${SHELLDECK_ARCHIVE:-}" ]; then
    archive="$(cd "$(dirname "$SHELLDECK_ARCHIVE")" && pwd)/$(basename "$SHELLDECK_ARCHIVE")"
    name="$(basename "$archive")"
    sums="$archive.sha256"
    [ -f "$sums" ] || sums=""
  else
    if [ -n "${SHELLDECK_VERSION:-}" ]; then
      tag="v${SHELLDECK_VERSION#v}"
    else
      fetch "https://api.github.com/repos/$repo/releases/latest" "$tmp/latest.json"
      tag="$(sed -n 's/.*"tag_name": *"\([^"]*\)".*/\1/p' "$tmp/latest.json" | head -n 1)"
      [ -n "$tag" ] || die "couldn't find the latest release"
    fi
    name="shelldeck-${tag#v}-linux-x64.tar.gz"
    url="https://github.com/$repo/releases/download/$tag/$name"
    say "downloading $name ..."
    archive="$tmp/$name"
    sums="$archive.sha256"
    fetch "$url" "$archive" || die "download failed: $url"
    fetch "$url.sha256" "$sums" || die "download failed: $url.sha256"
  fi
  ver="${name#shelldeck-}"
  ver="${ver%-linux-x64.tar.gz}"
  [ "$ver" != "$name" ] || die "unexpected archive name: $name"
  if [ -n "$sums" ]; then
    want="$(cut -d' ' -f1 "$sums")"
    if command -v sha256sum >/dev/null 2>&1; then got="$(sha256sum "$archive" | cut -d' ' -f1)"; else got="$(shasum -a 256 "$archive" | cut -d' ' -f1)"; fi
    [ "$want" = "$got" ] || die "checksum mismatch for $name (expected $want, got $got)"
    say "checksum ok"
  fi
  dest="$root/versions/$ver"
  if [ -x "$dest/sd" ]; then
    say "$ver is already unpacked"
  else
    mkdir -p "$tmp/out" "$root/versions"
    tar -xzf "$archive" -C "$tmp/out"
    rm -rf "$dest"  # a half-unpacked earlier try
    mv "$tmp/out/shelldeck-$ver" "$dest"
  fi
  printf %s "$ver" > "$root/current.txt"  # no newline: the shim reads it with cat
  # the shim, as shelldeck/frozen.py writes it (keep in sync): run versions/<current.txt>/sd
  printf '%s\n' '#!/bin/sh' 'd=$(dirname "$0")' 'exec "$d/versions/$(cat "$d/current.txt")/sd" "$@"' > "$root/sd"
  chmod 755 "$root/sd"
  "$dest/sd" --help >/dev/null || die "the unpacked sd did not run"
  case ":$PATH:" in
    *":$root:"*) ;;
    *)
      if [ -z "${SHELLDECK_NO_MODIFY_PATH:-}${UV_NO_MODIFY_PATH:-}" ]; then
        line="export PATH=\"$root:\$PATH\"  # shelldeck"
        for rc in "$HOME/.profile" "$HOME/.bashrc" "$HOME/.zshrc"; do
          if { [ -f "$rc" ] || [ "$rc" = "$HOME/.profile" ]; } && ! grep -qF "$line" "$rc" 2>/dev/null; then
            printf '\n%s\n' "$line" >> "$rc"
            say "added $root to your PATH in $rc"
          fi
        done
      fi
      say "Open a new terminal (or run: export PATH=\"$root:\$PATH\") if the command is not found."
      ;;
  esac
  say "installed shelldeck $ver in $root"
  if command -v uv >/dev/null 2>&1 && uv tool list 2>/dev/null | grep -q '^shelldeck '; then
    say "an older uv install of shelldeck is still there; remove it with: sd stop; uv tool uninstall shelldeck" "$c_warn"
  fi
}

if [ -z "$pip" ]; then
  case "$(uname -s)-$(uname -m)" in
    Linux-x86_64|Linux-amd64) ;;
    *) say "no standalone build for $(uname -s) $(uname -m) yet: installing the Python package"; pip=1 ;;
  esac
fi
if [ -n "$pip" ]; then install_pip; else install_standalone; fi

echo
say "shelldeck is installed." "$c_ok"
say "Run  sd  (or  shelldeck) to start it and open your browser. Already running? sd restart"
echo
