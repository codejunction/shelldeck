import time

import pytest
from fastapi.testclient import TestClient

from shelldeck import auth, plugins, server
from shelldeck.plugins import Plugin

demo = Plugin("demo", "1.0")
seen = []


@demo.command("hi", "Say hi")
def hi(args, ctx):
    return f"hi {' '.join(args)} from {ctx['cwd'] or '?'}"


@demo.command("type", "Typed, not run")
def typed(args, ctx):
    return {"input": "echo one\r\x1b[2Jrm -rf /"}


@demo.command("boom")
def boom(args, ctx):
    raise RuntimeError("nope")


@demo.on("handoff.")
def on_handoff(event):
    seen.append(event["type"])


@demo.on("handoff.")
def broken_handler(event):
    raise ValueError("a bad handler must not stop the others")


class EP:
    def __init__(self, name, obj):
        self.name, self.obj = name, obj

    def load(self):
        if isinstance(self.obj, Exception):
            raise self.obj
        return self.obj


@pytest.fixture
def client(tmp_path, monkeypatch):
    eps = [EP("demo", demo), EP("broken", ImportError("missing dep")), EP("wrong", object()), EP("off", Plugin("off"))]
    monkeypatch.setattr(plugins, "entry_points", lambda group: eps)
    monkeypatch.setenv("SHELLDECK_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(server, "ALLOWED_HOSTS", {"testserver"})
    with TestClient(server.app) as c:
        c.headers["X-Shelldeck-Token"] = auth.read_cli_token()
        yield c


def test_plugins(client):
    out = client.post("/api/plugins/off", json={"enabled": False}).json()
    status = {p["name"]: p["status"] for p in out["plugins"]}
    assert status["demo"] == "loaded" and status["off"] == "disabled"
    assert status["broken"] == "ImportError: missing dep" and status["wrong"].startswith("TypeError")
    assert [c["name"] for c in out["commands"]] == ["demo boom", "demo hi", "demo type"]

    run = lambda *w: client.post("/api/plugins/run", json={"words": list(w)})  # noqa: E731
    assert run("demo", "hi", "a", "b").json() == {"text": "hi a b from ?"}
    assert run("demo", "type").json() == {"input": "echo one"}  # one printable line: no CR, no escapes
    assert run("demo", "nope").status_code == 404 and run("other", "hi").status_code == 404
    assert run("demo", "boom").json() == {"error": "plugin_failed", "detail": "RuntimeError: nope"}
    assert client.post("/api/plugins/run", json={"words": "demo hi"}).status_code == 400

    server._emit("handoff.created", {"id": "h1"})
    for _ in range(50):
        if seen:
            break
        time.sleep(0.02)
    assert seen == ["handoff.created"]

    assert client.post("/api/plugins/nope", json={"enabled": True}).status_code == 404
    assert client.post("/api/plugins/off", json={"enabled": True}).json()["plugins"][-2]["status"] == "loaded"
