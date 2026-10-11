#!/bin/sh
# One-time setup inside WSL (Ubuntu 24.04): Swift, xtool and the iPhone tools. Safe to run again.
# Usage (in WSL):  sh ios/scripts/setup-wsl.sh
# Afterwards:      xtool auth login  (your Apple ID, then the 2FA code) and  xtool sdk install <Xcode.xip>
set -e
ARCH=$(uname -m)

echo "== apt packages (sudo asks for your WSL password)"
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
  curl git socat usbmuxd libimobiledevice-utils \
  zip gnupg2 libcurl4-openssl-dev libxml2-dev libncurses-dev libz3-dev >/dev/null

echo "== Swift (swiftly)"
if ! [ -x "$HOME/.local/share/swiftly/bin/swiftly" ]; then
  tmp=$(mktemp -d)
  curl -fsSL "https://download.swift.org/swiftly/linux/swiftly-$ARCH.tar.gz" -o "$tmp/swiftly.tar.gz"
  tar -C "$tmp" -xzf "$tmp/swiftly.tar.gz"
  "$tmp/swiftly" init --quiet-shell-followup --assume-yes
  rm -rf "$tmp"
fi
line='. "$HOME/.local/share/swiftly/env.sh"'
for f in "$HOME/.profile" "$HOME/.bashrc"; do grep -qF "$line" "$f" 2>/dev/null || echo "$line" >> "$f"; done
. "$HOME/.local/share/swiftly/env.sh"

echo "== xtool"
if ! command -v xtool >/dev/null; then
  curl -fsSL "https://github.com/xtool-org/xtool/releases/latest/download/xtool-$ARCH.AppImage" -o /tmp/xtool
  chmod +x /tmp/xtool
  sudo mv /tmp/xtool /usr/local/bin/xtool
fi

echo "== uv (for pymobiledevice3, which reveals Developer Mode on the phone)"
command -v uv >/dev/null || [ -x "$HOME/.local/bin/uv" ] || curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null
"$HOME/.local/bin/uv" tool install -q pymobiledevice3 2>/dev/null || true

swift --version | head -1
xtool --version
echo
echo "Done. Next:"
echo "  1. xtool auth login                 (Password mode works with a free Apple ID)"
echo "  2. xtool sdk install /path/to/Xcode_XX.xip"
echo "  3. On Windows: install 'Apple Devices' from the Microsoft Store, plug the iPhone in, tap Trust"
echo "  4. sh ios/scripts/bridge.sh         (connects WSL to the phone through Windows)"
echo "  5. sh ios/scripts/install.sh        (builds and installs the app)"
