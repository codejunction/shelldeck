#!/bin/sh
# An unsigned .ipa, for someone who signs and installs it themselves (Sideloadly, AltStore, or their own xtool:
# `xtool install shelldeck.ipa`, which signs with their Apple ID). Output: ios/xtool/Shelldeck.ipa
set -e
cd "$(dirname "$0")/.."
sh vendor.sh
xtool dev build -c release --ipa
ls -la xtool/*.ipa
