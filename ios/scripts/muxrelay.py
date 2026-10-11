"""One usbmuxd client connection: stdin/stdout <-> Apple's usbmuxd on Windows (127.0.0.1:27015).
Run by WSL once per connection (bridge.sh): socat UNIX-LISTEN:/var/run/usbmuxd,fork EXEC:<this, via python.exe>.
Stdlib only; it must be on a Windows drive so Windows python can open it."""
import os
import socket
import threading

s = socket.create_connection(("127.0.0.1", 27015))


def up():
    while data := os.read(0, 65536):
        s.sendall(data)
    os._exit(0)


threading.Thread(target=up, daemon=True).start()
while data := s.recv(65536):
    while data:
        data = data[os.write(1, data):]
os._exit(0)
