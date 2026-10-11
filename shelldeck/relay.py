"""Tell trusted iOS devices where the share is: the tunnel URL changes on every `sd share`.

Each device has a random ntfy.sh topic and an AES-256-GCM key, both made at enrolment and held only by
this server and the device's Keychain. The message is base64(nonce(12) | ciphertext | tag(16)) of
{"url", "at"}, the layout CryptoKit's AES.GCM.SealedBox(combined:) reads. ntfy.sh sees only ciphertext
and timing; GCM also stops anyone who guesses a topic from steering the app to a page of their own.
"""

import base64
import json
import logging
import os
import secrets
import time
import urllib.request

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

log = logging.getLogger(__name__)

NTFY = "https://ntfy.sh"
REPEAT_S = 6 * 3600  # ntfy.sh keeps messages 12h: repeat while sharing so a phone opened later still finds it


def new_device() -> tuple[str, str]:
    """(topic, base64 key) for a new device."""
    return secrets.token_urlsafe(24), base64.b64encode(AESGCM.generate_key(bit_length=256)).decode()


def seal(key: str, payload: dict) -> str:
    nonce = os.urandom(12)
    return base64.b64encode(nonce + AESGCM(base64.b64decode(key)).encrypt(nonce, json.dumps(payload).encode(), None)).decode()


def open_(key: str, message: str) -> dict:
    raw = base64.b64decode(message)
    return json.loads(AESGCM(base64.b64decode(key)).decrypt(raw[:12], raw[12:], None))


def publish(devices: list[dict], url: str) -> None:
    """Post the share URL to every device's topic. Blocking: run it on a thread. ponytail: no retry, the next
    repeat (REPEAT_S) or share start tries again."""
    for d in devices:
        body = seal(d["key"], {"url": url, "at": time.time()}).encode()
        req = urllib.request.Request(f"{NTFY}/{d['topic']}", data=body, method="POST")
        try:
            urllib.request.urlopen(req, timeout=10).close()
        except OSError as e:
            log.warning("relay to device %s failed: %s", d["id"], e)
