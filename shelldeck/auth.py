"""Login for shelldeck: one password, per-browser sessions, a host-only CLI token.

- No password yet: the first visit creates one. From anywhere but the host itself that also
  needs the setup code printed on the host (and kept in <config>/setup-code), so nobody else
  can claim a fresh remote server first.
- Each browser login is a random token in an HttpOnly cookie. Only its sha256 is stored
  (auth_sessions table), so logins survive restarts and a stolen database holds no tokens.
  A session idle for LOCK_TIMEOUT_SECONDS is dropped: that browser is locked.
- The CLI on the host reads <config>/cli-token (owner-only) and sends it as a header.
- Emergency (forgotten password): `shelldeck reset-password` on the host. See README.
"""

import hashlib
import hmac
import os
import secrets
import time

from . import db

LOCK_TIMEOUT_SECONDS = 30 * 60
MIN_PASSWORD = 8
COOKIE = "sd_session"
TOKEN_HEADER = "x-shelldeck-token"
_PASSWORD_KEY = "auth_password_hash"
_TOUCH_EVERY = 60  # seconds; last_seen writes are throttled
_CACHE_TTL = 5  # seconds a session lookup is trusted before re-reading the db

_cache: dict[str, tuple[float, dict | None]] = {}
_failures: dict[str, tuple[int, float]] = {}  # client -> (count, blocked until)


# ------------------------------------------------------------------ password


def _hash_password(password: str) -> str:
    salt = os.urandom(32)
    iterations = 600_000
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"{salt.hex()}:{iterations}:{digest.hex()}"


def _verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, iterations_s, hash_hex = stored.split(":")
        salt = bytes.fromhex(salt_hex)
        iterations = int(iterations_s)
    except ValueError:
        return False
    computed = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return hmac.compare_digest(computed.hex(), hash_hex)


def has_password() -> bool:
    return bool(db.get_setting(_PASSWORD_KEY))


def check_password(password: str) -> bool:
    stored = db.get_setting(_PASSWORD_KEY)
    return bool(stored) and _verify_password(password, stored)


def validate_new(password: str, confirm: str) -> str | None:
    """Error code for an unacceptable new password, else None."""
    if len(password) < MIN_PASSWORD:
        return "password_too_short"
    if password != confirm:
        return "passwords_do_not_match"
    return None


def set_password(password: str) -> None:
    db.set_setting(_PASSWORD_KEY, _hash_password(password))
    _file("setup-code").unlink(missing_ok=True)


# ------------------------------------------------------------------ host files


def _file(name: str):
    return db.config_dir() / name


def _write_private(name: str, text: str) -> None:
    path = _file(name)
    path.write_text(text, encoding="utf-8")
    try:
        os.chmod(path, 0o600)  # ponytail: on Windows the user profile folder is already private
    except OSError:
        pass


def setup_code() -> str | None:
    """The one-time code remote setup needs; created while no password exists."""
    if has_password():
        return None
    path = _file("setup-code")
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    code = "-".join(secrets.token_hex(2) for _ in range(3))
    _write_private("setup-code", code)
    return code


def cli_token() -> str:
    """Token the local CLI sends; rotated on every server start."""
    token = secrets.token_urlsafe(32)
    _write_private("cli-token", token)
    return token


def read_cli_token() -> str:
    try:
        return _file("cli-token").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def init_auth() -> str | None:
    """On server start: drop termy's seeded password, rotate the CLI token. Returns the setup code, if any."""
    stored = db.get_setting(_PASSWORD_KEY)
    if stored and _verify_password("nopassword", stored):
        db.delete_setting(_PASSWORD_KEY)
    cli_token()
    return setup_code()


def reset() -> str:
    """Emergency, host-only: forget the password and every login. Returns the new setup code."""
    db.delete_setting(_PASSWORD_KEY)
    db.delete_auth_sessions()
    _file("setup-code").unlink(missing_ok=True)
    _cache.clear()
    return setup_code() or ""


# ------------------------------------------------------------------ sessions


def _h(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_session(client: str) -> str:
    token = secrets.token_urlsafe(32)
    db.add_auth_session(_h(token), time.time(), client)
    return token


def session_hash(token: str | None) -> str | None:
    """sha256 of a live session token, else None. Idle sessions are removed here."""
    if not token:
        return None
    h = _h(token)
    now = time.time()
    hit = _cache.get(h)
    row = hit[1] if hit and now - hit[0] < _CACHE_TTL else db.get_auth_session(h)
    _cache[h] = (now, row)
    if not row:
        return None
    if now - row["last_seen"] > LOCK_TIMEOUT_SECONDS:
        end_session(h)
        return None
    return h


def touch(h: str) -> None:
    """User activity on a session (keeps it from locking)."""
    now = time.time()
    row = _cache.get(h, (0, None))[1]
    if row and now - row["last_seen"] > _TOUCH_EVERY:
        row["last_seen"] = now
        db.touch_auth_session(h, now)


def end_session(h: str) -> None:
    db.delete_auth_sessions(only=h)
    _cache.pop(h, None)


def end_other_sessions(keep: str) -> None:
    db.delete_auth_sessions(keep=keep)
    _cache.clear()


def cli_ok(token: str | None) -> bool:
    expected = read_cli_token()
    return bool(token and expected) and hmac.compare_digest(token, expected)


# ------------------------------------------------------------------ brute force


def blocked_for(client: str) -> int:
    """Seconds this client must wait before trying a password again."""
    count, until = _failures.get(client, (0, 0.0))
    return max(0, int(until - time.time() + 0.999))


def record_attempt(client: str, ok: bool) -> None:
    if ok:
        _failures.pop(client, None)
        return
    count = _failures.get(client, (0, 0.0))[0] + 1
    # 5 free tries, then 2, 4, 8 … seconds, capped at 5 minutes
    wait = 0 if count < 5 else min(300, 2 ** (count - 4))
    _failures[client] = (count, time.time() + wait)
