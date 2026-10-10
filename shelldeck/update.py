"""Updates. The standalone build updates itself in place: the new version is unpacked next to the running
one (<root>/versions/<ver>/, Windows locks the running files), <root>/current.txt points at it and sd-ui
restarts from there while sd-pty keeps the shells. A PyPI install is told to run `uv tool upgrade`.
"""

import hashlib
import json
import os
import platform
import re
import shutil
import sys
import tarfile
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

from . import frozen

REPO = "codejunction/shelldeck"
RELEASES = f"https://api.github.com/repos/{REPO}/releases/latest"
PYPI = "https://pypi.org/pypi/shelldeck/json"
CHECK_S = 6 * 3600
_cache: dict = {}


def root() -> Path | None:
    """<root> of a standalone install (this exe lives in <root>/versions/<ver>/), else None."""
    return frozen.root() if frozen.FROZEN else None


def app_dir() -> Path | None:
    """Where sd-ui starts from: the version current.txt names, else next to this program."""
    if (r := root()) and (cur := r / "current.txt").exists():
        d = r / "versions" / cur.read_text(encoding="utf-8").strip()
        if d.is_dir():
            return d
    return None


def _key(v: str) -> tuple:
    """'0.0.12' -> (0, 0, 12, 0); a suffix (rc1, dev) sorts it just below the release."""
    m = re.match(r"v?(\d+(?:\.\d+)*)(.*)", v)
    return (*map(int, m[1].split(".")), -1 if m[2] else 0) if m else (0,)


def newer(latest: str, current: str) -> bool:
    return current != "dev" and _key(latest) > _key(current)


def _get(url: str, timeout: float = 15):
    req = urllib.request.Request(url, headers={"User-Agent": "shelldeck", "Accept": "application/json"})
    return urllib.request.urlopen(req, timeout=timeout)  # noqa: S310 - fixed https URLs


def asset_name(version: str) -> str:
    arch = {"amd64": "x64", "x86_64": "x64", "arm64": "arm64", "aarch64": "arm64"}.get(platform.machine().lower(), platform.machine().lower())
    return f"shelldeck-{version}-windows-{arch}.zip" if sys.platform == "win32" else f"shelldeck-{version}-linux-{arch}.tar.gz"


def check(current: str, force: bool = False) -> dict:
    """{current, latest, available, how}: how is 'ota' (standalone, updates in place), 'manual'
    (standalone unpacked outside versions/: download the new release) or 'uv' (PyPI install)."""
    if not force and _cache.get("at", 0) > time.time() - CHECK_S and _cache.get("current") == current:
        return _cache["result"]
    how = "ota" if root() else "manual" if frozen.FROZEN else "uv"  # manual: an unpacked build outside versions/
    if how != "uv":
        with _get(RELEASES) as r:
            release = json.load(r)
        latest = release["tag_name"].lstrip("v")
        assets = {a["name"]: a["browser_download_url"] for a in release.get("assets", [])}
        ok = asset_name(latest) in assets and asset_name(latest) + ".sha256" in assets
    else:
        with _get(PYPI) as r:
            latest, assets, ok = json.load(r)["info"]["version"], {}, True
    result = {"current": current, "latest": latest, "available": ok and newer(latest, current), "how": how}
    _cache.update(at=time.time(), current=current, result=result, assets=assets)
    return result


def apply(current: str) -> str:
    """Download, verify and unpack the latest release next to this one, then point current.txt at it.
    Returns the new version; the caller restarts sd-ui."""
    r = root()
    if not r:
        raise RuntimeError("not a standalone install; run: uv tool upgrade shelldeck")
    info = check(current, force=True)
    if not info["available"]:
        raise RuntimeError("already up to date")
    version, name, assets = info["latest"], asset_name(info["latest"]), _cache["assets"]
    with _get(assets[name + ".sha256"]) as resp:
        want = resp.read().decode().split()[0].lower()
    versions = r / "versions"
    with tempfile.TemporaryDirectory(dir=versions, prefix=".download-") as tmp:
        archive = Path(tmp) / name
        digest = hashlib.sha256()
        with _get(assets[name], timeout=60) as resp, archive.open("wb") as f:
            while chunk := resp.read(1 << 20):
                digest.update(chunk)
                f.write(chunk)
        if digest.hexdigest() != want:
            raise RuntimeError("download failed its sha256 check")
        out = Path(tmp) / "x"
        if name.endswith(".zip"):
            with zipfile.ZipFile(archive) as z:
                z.extractall(out)  # zipfile drops absolute and '..' parts
        else:
            with tarfile.open(archive) as t:
                t.extractall(out, filter="data")
        top = out / f"shelldeck-{version}"
        if not (top / ("sd-ui.exe" if sys.platform == "win32" else "sd-ui")).exists():
            raise RuntimeError("the release archive has an unexpected layout")
        dest = versions / version
        if dest.exists():
            shutil.rmtree(dest)  # an earlier, unfinished attempt (a running version is never `latest`)
        os.replace(top, dest)
    cur = r / "current.txt"
    (tmp_cur := cur.with_suffix(".tmp")).write_text(version, encoding="utf-8")
    os.replace(tmp_cur, cur)
    # ponytail: old versions stay on disk; drop the ones nothing runs from once updates are routine
    return version
