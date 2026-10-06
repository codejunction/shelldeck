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


@demo.command("quit")
def quit_(args, ctx):
    raise SystemExit(3)  # a plugin's sys.exit must not stop the server


@demo.on("handoff.")
def on_handoff(event):
    seen.append(event["type"])


@demo.on("handoff.")
def broken_handler(event):
    raise SystemExit("a bad handler must not stop the others")


class EP:
    def __init__(self, name, obj):
        self.name, self.obj = name, obj

    def load(self):
        if isinstance(self.obj, BaseException):
            raise self.obj
        return self.obj


@pytest.fixture
def client(tmp_path, monkeypatch):
    eps = [EP("demo", demo), EP("broken", ImportError("missing dep")), EP("wrong", object()), EP("exits", SystemExit(1)),
           EP("other-name", Plugin("demo2")), EP("off", Plugin("off"))]
    monkeypatch.setattr(plugins, "entry_points", lambda group: eps)
    monkeypatch.setenv("SHELLDECK_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(server, "ALLOWED_HOSTS", {"testserver"})
    with TestClient(server.app) as c:
        c.headers["X-Shelldeck-Token"] = auth.read_cli_token()
        yield c


def test_plugins(client):
    out = client.post("/api/plugins/off", json={"enabled": False}).json()
    by = {p["name"]: p for p in out["plugins"]}
    assert by["demo"]["status"] == "loaded" and by["demo"]["version"] == "1.0" and by["demo"]["events"] == ["handoff."]
    assert by["off"]["status"] == "disabled" and by["off"]["commands"] == []
    assert by["broken"]["error"] == "ImportError: missing dep" and by["exits"]["error"] == "SystemExit: 1"
    assert by["wrong"]["status"] == by["other-name"]["status"] == "error"
    assert [c["name"] for c in out["commands"]] == ["demo boom", "demo hi", "demo quit", "demo type"]

    run = lambda *w: client.post("/api/plugins/run", json={"words": list(w)})  # noqa: E731
    assert run("demo", "hi", "a", "b").json() == {"text": "hi a b from ?"}
    assert run("demo", "type").json() == {"input": "echo one"}  # one printable line: no CR, no escapes
    assert run("demo", "nope").status_code == 404 and run("other", "hi").status_code == 404
    assert run("demo", "boom").json() == {"error": "plugin_failed", "detail": "RuntimeError: nope"}
    assert run("demo", "quit").json() == {"error": "plugin_failed", "detail": "SystemExit: 3"}
    assert client.post("/api/plugins/run", json={"words": "demo hi"}).status_code == 400

    server._emit("handoff.created", {"id": "h1"})
    for _ in range(50):
        if seen:
            break
        time.sleep(0.02)
    assert seen == ["handoff.created"]

    assert client.post("/api/plugins/nope", json={"enabled": True}).status_code == 404
    out = client.post("/api/plugins/off", json={"enabled": True}).json()
    assert {p["name"]: p["status"] for p in out["plugins"]}["off"] == "loaded"


def test_ask_can_answer_with_a_plugin_command(client, monkeypatch):
    from shelldeck import smart_recall
    prompts, reply = [], ["?demo hi Ada"]
    monkeypatch.setattr(smart_recall, "pick", lambda setting: "claude")
    monkeypatch.setattr(smart_recall, "argv", lambda agent, q, model, prompt: prompts.append(prompt) or ["x"])
    monkeypatch.setattr(smart_recall, "_run", lambda cmd: reply[0])
    out = client.post("/api/ask", json={"q": "greet Ada"}).json()
    assert out["plugin"] == ["demo", "hi", "Ada"] and out["command"] == ""
    assert "?demo hi  - Say hi" in prompts[0]  # plugin commands are offered to the model
    reply[0] = "?nope run"  # a made-up plugin command doesn't count
    assert client.post("/api/ask", json={"q": "x"}).json()["error"] == "no_answer"
    reply[0] = "Get-Date"
    assert client.post("/api/ask", json={"q": "date"}).json() == {"command": "Get-Date", "plugin": None, "agent": "claude"}
