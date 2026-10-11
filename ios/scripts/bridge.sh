#!/bin/sh
# Connect WSL to the iPhone through Windows: /var/run/usbmuxd in WSL becomes Apple's usbmuxd on Windows
# (the Apple Devices app, 127.0.0.1:27015), via socat and a tiny Windows Python relay (muxrelay.py).
# Why not usbipd: Linux usbmuxd over USB/IP sends 65536-byte packets that iOS rejects (65535 max), so
# installs time out (libimobiledevice/usbmuxd#247). Needs Python on Windows. Run again after a WSL restart.
# Usage (in WSL):  sh ios/scripts/bridge.sh     WIN_PYTHON=/mnt/c/Python313/python.exe to pick a Python.
set -e
here=$(cd "$(dirname "$0")" && pwd)

# a Windows Python (WSL can run .exe files)
py=${WIN_PYTHON:-$(cmd.exe /c "where python" 2>/dev/null | tr -d '\r' | grep -iv WindowsApps | head -1)}
case "$py" in [A-Za-z]:\\*) py=$(wslpath -u "$py") ;; esac
[ -x "$py" ] || { echo "No Windows Python found. Install it (winget install Python.Python.3.13) or set WIN_PYTHON." >&2; exit 1; }

# the relay must be on a Windows drive for Windows Python to open it
winhome=$(wslpath -u "$(cmd.exe /c 'echo %USERPROFILE%' 2>/dev/null | tr -d '\r')")
mkdir -p "$winhome/.xtool-bridge"
cp "$here/muxrelay.py" "$winhome/.xtool-bridge/muxrelay.py"
relay=$(wslpath -w "$winhome/.xtool-bridge/muxrelay.py")
wrapper=$(mktemp)
printf '#!/bin/sh\nexec "%s" -u '"'"'%s'"'"'\n' "$py" "$relay" > "$wrapper"
sudo install -m 755 "$wrapper" /usr/local/bin/muxrelay
rm -f "$wrapper"

# Linux usbmuxd must not hold the socket
sudo systemctl mask --runtime usbmuxd.service >/dev/null 2>&1 || true
sudo pkill -9 usbmuxd 2>/dev/null || true
sudo pkill -f "socat UNIX-LISTEN:/var/run/usbmuxd" 2>/dev/null || true
sleep 1
sudo rm -f /var/run/usbmuxd
sudo sh -c 'nohup socat UNIX-LISTEN:/var/run/usbmuxd,fork,mode=666 EXEC:/usr/local/bin/muxrelay > /tmp/xtool-bridge.log 2>&1 &'
sleep 2

if idevice_id -l | grep -q .; then
  echo "Bridge up. iPhone(s):"
  for id in $(idevice_id -l); do echo "  $id  iOS $(ideviceinfo -u "$id" -k ProductVersion 2>/dev/null)"; done
else
  echo "Bridge up, but no iPhone yet: open Apple Devices on Windows, plug the phone in, unlock it and tap Trust."
fi
