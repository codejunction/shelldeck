#!/bin/sh
# Build the app and install it on a connected iPhone or iPad (signed with the Apple ID of `xtool auth login`).
# Usage (in WSL, from anywhere):
#   sh ios/scripts/install.sh                 the only connected device
#   sh ios/scripts/install.sh <udid>          a specific one (`idevice_id -l` lists them)
#   sh ios/scripts/install.sh --devmode [udid]   first time on a device: make Settings show Developer Mode
set -e
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:$PATH" PYMOBILEDEVICE3_USBMUX=/var/run/usbmuxd

if [ "$1" = "--devmode" ]; then
  # iOS/iPadOS only show the switch (Settings > Privacy & Security > Developer Mode) after a computer asks
  udid=${2:+--udid "$2"}
  # it reports some failures (a locked device) only in its output, not its exit code
  out=$(pymobiledevice3 amfi reveal-developer-mode $udid 2>&1) || true
  case "$out" in
    *ERROR*|*rror:*) echo "$out" >&2; echo "Not revealed: unlock the device (and tap Trust if asked), then run this again." >&2; exit 1 ;;
  esac
  echo "Revealed. On the device:"
  echo "  1. Close Settings completely (swipe it away in the app switcher): it only rereads this on a fresh launch."
  echo "  2. Settings > Privacy & Security > scroll to the bottom > Developer Mode > on > Restart."
  echo "  3. After the restart, unlock, tap Turn On and enter the passcode."
  echo "  (A device without a passcode can be switched on from here instead:"
  echo "   pymobiledevice3 amfi enable-developer-mode $udid   -- it restarts the device.)"
  exit 0
fi

idevice_id -l | grep -q . || { echo "No device. Run scripts/bridge.sh, plug the phone in and unlock it." >&2; exit 1; }
if [ "$(ideviceinfo ${1:+-u "$1"} -q com.apple.security.mac.amfi -k DeveloperModeStatus 2>/dev/null)" = "false" ]; then
  echo "Developer Mode is off on that device: run  sh ios/scripts/install.sh --devmode  first." >&2
  exit 1
fi

sh vendor.sh
xtool dev run -c release --usb ${1:+--udid "$1"}
echo
echo "Installed. On the device, the first time: Settings > General > VPN & Device Management > your Apple ID > Trust."
echo "A free Apple ID's apps stop opening after 7 days: run this again (data and trust are kept)."
