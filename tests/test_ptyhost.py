"""sd-pty / sd-ui split: a shell outlives its UI connection, and a new UI gets it back."""

import asyncio

from shelldeck import ptyhost, update


async def _until(pred, timeout=10.0):
    end = asyncio.get_running_loop().time() + timeout
    while not pred():
        assert asyncio.get_running_loop().time() < end, "timed out"
        await asyncio.sleep(0.05)


def test_shell_survives_ui_reconnect(tmp_path):
    async def main():
        host = ptyhost.Host("tok", tmp_path)
        server = await asyncio.start_server(host.serve_client, "127.0.0.1", 0, limit=ptyhost.LIMIT)
        pump = asyncio.create_task(host.pump())
        addr = f"127.0.0.1:{server.sockets[0].getsockname()[1]}"
        alive: list[str] = []
        try:
            ui = ptyhost.PtyClient(addr, "tok")
            await ui.connect(alive.append, lambda: None)
            got: list[str | None] = []
            ui.create("s1", rows=24, cols=80, extra_env={"SHELLDECK_AGENT_REPORT_TOKEN": "r"})
            ui.stream("s1", got.append)
            await _until(lambda: ui.procs["s1"].pid)
            ui.write("s1", "echo first-ui\r")
            await _until(lambda: "first-ui" in ui.history("s1").split("echo first-ui")[-1])
            pid = ui.procs["s1"].pid
            ui.writer.close()  # the UI restarts

            ui2 = ptyhost.PtyClient(addr, "tok")
            await ui2.connect(alive.append, lambda: None)
            assert alive == ["s1"] and ui2.procs["s1"].pid == pid
            assert ui2.env["s1"]["SHELLDECK_AGENT_REPORT_TOKEN"] == "r"
            assert "first-ui" in ui2.history("s1")
            ui2.write("s1", "echo second-ui\r")
            await _until(lambda: "second-ui" in ui2.history("s1").split("echo second-ui")[-1])
            ui2.terminate("s1")
            await _until(lambda: "s1" not in host.manager.procs)

            bad = ptyhost.PtyClient(addr, "wrong")
            try:
                await bad.connect(alive.append, lambda: None)
                raise AssertionError("a wrong token must not connect")
            except (ConnectionError, ValueError):
                pass
        finally:
            pump.cancel()
            server.close()
            host.shutdown()

    asyncio.run(main())


def test_update_versions():
    assert update.newer("0.0.12", "0.0.11") and update.newer("0.1.0", "0.0.99")
    assert not update.newer("0.0.11", "0.0.11") and not update.newer("9.9.9", "dev")
    assert update.newer("0.0.12", "0.0.12rc1") and not update.newer("0.0.12rc1", "0.0.12")


def test_update_apply_installs_next_to_running(tmp_path, monkeypatch):
    import hashlib
    import io
    import json
    import sys
    import zipfile

    import pytest

    from shelldeck import frozen

    root = tmp_path / "root"
    (root / "versions" / "0.0.11").mkdir(parents=True)
    (root / "current.txt").write_text("0.0.11")
    name = update.asset_name("0.0.12")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        exe = "sd-ui.exe" if sys.platform == "win32" else "sd-ui"
        z.writestr(f"shelldeck-0.0.12/{exe}", "new")
        z.writestr("shelldeck-0.0.12/_internal/x.txt", "x")
    archive = buf.getvalue()
    if name.endswith(".tar.gz"):  # the Linux asset: same tree as a tarball
        import tarfile
        tbuf = io.BytesIO()
        with tarfile.open(fileobj=tbuf, mode="w:gz") as t, zipfile.ZipFile(io.BytesIO(archive)) as z:
            for n in z.namelist():
                info = tarfile.TarInfo(n)
                data = z.read(n)
                info.size = len(data)
                t.addfile(info, io.BytesIO(data))
        archive = tbuf.getvalue()
    digest = {"v": hashlib.sha256(archive).hexdigest()}
    urls = {"https://x/a": lambda: archive, "https://x/s": lambda: f"{digest['v']} *{name}\n".encode(),
            update.RELEASES: lambda: json.dumps({"tag_name": "v0.0.12", "assets": [
                {"name": name, "browser_download_url": "https://x/a"},
                {"name": name + ".sha256", "browser_download_url": "https://x/s"}]}).encode()}
    monkeypatch.setattr(update, "_get", lambda url, timeout=15: io.BytesIO(urls[url]()))
    monkeypatch.setattr(frozen, "FROZEN", True)
    monkeypatch.setattr(frozen, "root", lambda: root)
    update._cache.clear()

    digest["v"] = "0" * 64  # a tampered download is refused and changes nothing
    with pytest.raises(RuntimeError, match="sha256"):
        update.apply("0.0.11")
    assert (root / "current.txt").read_text() == "0.0.11" and not (root / "versions" / "0.0.12").exists()

    digest["v"] = hashlib.sha256(archive).hexdigest()
    assert update.apply("0.0.11") == "0.0.12"
    assert (root / "current.txt").read_bytes() == b"0.0.12"  # no newline: the sh shim cats it
    assert (root / "versions" / "0.0.12" / "_internal" / "x.txt").exists()
    assert update.app_dir() == root / "versions" / "0.0.12"
    assert [p.name for p in (root / "versions").iterdir()] == ["0.0.11", "0.0.12"]
