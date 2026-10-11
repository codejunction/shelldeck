"""End-to-end encryption between the iOS app and this server, so the share's tunnel (Cloudflare decrypts TLS at
its edge) and the home Wi-Fi only ever carry ciphertext.

The server has a long-lived X25519 key (<config>/e2e-key, owner-only). Its public half travels in the share QR
code's #fragment, which browsers and Cloudflare never see, so the app pins it from the camera; an app added on
home Wi-Fi learns it there on first use. Each channel (`/e2e` WebSocket, server.py):

  client -> server   client ephemeral public key (32 bytes)
  server -> client   server ephemeral public key (32 bytes)
  both               HKDF-SHA256(static x client_eph || server_eph x client_eph,
                                 salt = client_eph | server_eph, info = INFO) -> 64 bytes
                     first 32: client->server key, last 32: server->client key

Only the holder of the static private key can derive the keys (Cloudflare can't sit in the middle), and the
ephemeral half gives forward secrecy. Every frame after that is AES-256-GCM with a 96-bit big-endian counter as
the nonce, per direction: nonces never repeat, and a dropped, replayed or reordered frame fails to decrypt.
Clients still authenticate inside the channel exactly as before (share grant or trusted device, then password).
"""

import base64
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from . import auth, db

INFO = b"shelldeck e2e v1"
KEY_FILE = "e2e-key"


def _raw(key: X25519PublicKey) -> bytes:
    return key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def server_key() -> X25519PrivateKey:
    """This server's static key, made on first use."""
    path: Path = db.config_dir() / KEY_FILE
    try:
        return X25519PrivateKey.from_private_bytes(base64.b64decode(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        key = X25519PrivateKey.generate()
        raw = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
        auth._write_private(KEY_FILE, base64.b64encode(raw).decode())
        return key


def public() -> str:
    """The static public key, base64url without padding (as it goes in the QR fragment: #k=...)."""
    return base64.urlsafe_b64encode(_raw(server_key().public_key())).rstrip(b"=").decode()


class Session:
    """One channel's keys (server side)."""

    def __init__(self, static: X25519PrivateKey, client_eph: bytes):
        peer = X25519PublicKey.from_public_bytes(client_eph)  # ValueError unless 32 bytes
        eph = X25519PrivateKey.generate()
        self.reply = _raw(eph.public_key())
        okm = HKDF(hashes.SHA256(), 64, salt=client_eph + self.reply, info=INFO).derive(static.exchange(peer) + eph.exchange(peer))
        self._in, self._out = AESGCM(okm[:32]), AESGCM(okm[32:])
        self._n_in = self._n_out = 0

    def open(self, frame: bytes) -> bytes:
        """Raises cryptography.exceptions.InvalidTag for anything not sealed next by the client."""
        data = self._in.decrypt(self._n_in.to_bytes(12, "big"), frame, None)
        self._n_in += 1
        return data

    def seal(self, data: bytes) -> bytes:
        frame = self._out.encrypt(self._n_out.to_bytes(12, "big"), data, None)
        self._n_out += 1
        return frame


class Client:
    """The app's side, for tests (the app does the same with CryptoKit)."""

    def __init__(self, server_public: bytes):
        self._static = X25519PublicKey.from_public_bytes(server_public)
        self._eph = X25519PrivateKey.generate()
        self.hello = _raw(self._eph.public_key())

    def finish(self, reply: bytes) -> None:
        peer = X25519PublicKey.from_public_bytes(reply)
        okm = HKDF(hashes.SHA256(), 64, salt=self.hello + reply, info=INFO).derive(self._eph.exchange(self._static) + self._eph.exchange(peer))
        self._out, self._in = AESGCM(okm[:32]), AESGCM(okm[32:])
        self._n_in = self._n_out = 0

    def seal(self, data: bytes) -> bytes:
        frame = self._out.encrypt(self._n_out.to_bytes(12, "big"), data, None)
        self._n_out += 1
        return frame

    def open(self, frame: bytes) -> bytes:
        data = self._in.decrypt(self._n_in.to_bytes(12, "big"), frame, None)
        self._n_in += 1
        return data
