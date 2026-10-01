import json
import subprocess

import pytest
from typer.testing import CliRunner

from shelldeck import cli

runner = CliRunner()


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("SHELLDECK_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("FC_CACHE_DIR", str(tmp_path / "fc"))
    monkeypatch.setitem(cli.CFG, "port", 5499)
    return tmp_path


def test_banner_loopback_has_no_network_url(capsys):
    cli._banner("127.0.0.1", "started")
    out = capsys.readouterr().out
    assert "|___/" in out and "started" in out
    assert "http://127.0.0.1:5499" in out
    assert "Network:  off" in out
    out.encode("ascii")  # piped output on Windows is cp1252; keep the banner ASCII


def test_banner_all_interfaces_shows_lan_url(capsys, monkeypatch):
    monkeypatch.setattr(cli, "_lan_ip", lambda: "192.168.9.9")
    cli._banner("0.0.0.0", "foreground", "https")
    assert "https://192.168.9.9:5499" in capsys.readouterr().out


def test_web_starts_server_prints_banner_and_opens(monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "_health", lambda: None)
    monkeypatch.setattr(cli, "ensure_server", lambda: calls.append("server"))
    monkeypatch.setattr(cli, "_banner", lambda host, status, scheme="": calls.append(status))
    monkeypatch.setattr(cli, "open_window", lambda path="/": calls.append(path))
    assert runner.invoke(cli.app, ["web"]).exit_code == 0
    assert calls == ["server", "started", "/"]


def test_browser_by_default_app_window_on_request(monkeypatch):
    opened = []
    monkeypatch.setattr(cli.webbrowser, "open", lambda url: opened.append(("browser", url)))
    monkeypatch.setattr(cli, "_browser", lambda: "edge.exe")
    monkeypatch.setattr(cli.subprocess, "Popen", lambda argv, **kw: opened.append(("app", argv[1])))
    monkeypatch.setitem(cli.CFG, "app", False)
    cli.open_window("/")
    monkeypatch.setitem(cli.CFG, "app", True)
    cli.open_window("/")
    assert opened == [("browser", "http://127.0.0.1:5499/"), ("app", "--app=http://127.0.0.1:5499/")]


def test_own_console_false_without_a_tty():
    # pytest captures stdin, so this is never "a console Windows opened just for sd"
    assert cli._own_console() is False


def test_serve_refuses_plain_http_off_loopback():
    r = runner.invoke(cli.app, ["serve", "--host", "0.0.0.0"])
    assert r.exit_code == 2 and "refusing to serve plain HTTP" in r.output


def test_serve_needs_cert_and_key_together(tmp_path):
    cert = tmp_path / "c.pem"
    cert.write_text("x")
    r = runner.invoke(cli.app, ["serve", "--cert", str(cert)])
    assert r.exit_code == 2 and "go together" in r.output


def test_render_markdown_and_code(tmp_path):
    md, py = tmp_path / "a.md", tmp_path / "b.py"
    md.write_text("# Title\n\nbody", encoding="utf-8")
    py.write_text("def hello():\n    return 1\n", encoding="utf-8")
    assert "Title" in runner.invoke(cli.app, ["render", str(md)]).output
    assert "hello" in runner.invoke(cli.app, ["render", str(py)]).output


@pytest.fixture
def codebase(tmp_path):
    root = tmp_path / "code"
    (root / "src").mkdir(parents=True)
    (root / "src" / "auth.py").write_text(
        "def validate_token(token):\n    # check the auth token signature\n    return token.startswith('ok')\n",
        encoding="utf-8",
    )
    (root / "src" / "util.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
    return root


def test_search_formats(codebase):
    r = runner.invoke(cli.app, ["search", "validate token", "--root", str(codebase), "-f", "json"])
    assert r.exit_code == 0
    out = json.loads(r.output)
    top = out["results"][0]
    assert top["path"] == "src/auth.py"
    assert top["snippets"][0]["start_line"] == 1

    r = runner.invoke(cli.app, ["search", "validate token", "--root", str(codebase), "-f", "paths"])
    assert "src/auth.py:1-" in r.output

    r = runner.invoke(cli.app, ["search", "validate token", "--root", str(codebase)])
    assert r.exit_code == 0 and "src/auth.py" in r.output and "Confidence" in r.output


def test_search_rejects_bad_format(codebase):
    r = runner.invoke(cli.app, ["search", "x", "--root", str(codebase), "-f", "xml"])
    assert r.exit_code == 1


LINK = {"url": "https://abc-def.trycloudflare.com/share/tok", "path": "/share/tok"}


def _share_api(calls, sharing=False, strong=True, terms=True):
    def api(path, method="GET", payload=None, timeout=10):
        calls.append((method, path))
        if path == "/api/auth/status":
            return {"has_password": True}
        on = sharing or ("POST", "/api/share") in calls
        return {"sharing": on, "link": LINK if on else None, "strong_password": strong, "cloudflared": True, "pending": [],
                "terms_accepted": terms or ("POST", "/api/share/terms") in calls, "terms": ["at your own risk"]}
    return api


def _interrupt(stopped):
    raise KeyboardInterrupt


def test_share_starts_on_the_server_and_stops_on_ctrl_c(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(cli, "ensure_server", lambda: None)
    monkeypatch.setattr(cli, "_health", lambda: "ok")
    monkeypatch.setattr(cli, "_api", _share_api(calls))
    monkeypatch.setattr(cli, "_watch_share", _interrupt)
    cli.share(action=None, new_link=False)
    assert LINK["url"] in capsys.readouterr().out
    assert ("POST", "/api/share") in calls and calls[-1] == ("DELETE", "/api/share")


def test_share_asks_for_terms(monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "ensure_server", lambda: None)
    monkeypatch.setattr(cli, "_api", _share_api(calls, terms=False))
    r = runner.invoke(cli.app, ["share"], input="n\n")
    assert r.exit_code == 1 and "own risk" in r.output and ("POST", "/api/share") not in calls
    monkeypatch.setattr(cli, "_watch_share", _interrupt)
    monkeypatch.setattr(cli, "_health", lambda: "ok")
    assert runner.invoke(cli.app, ["share"], input="y\n").exit_code == 0
    assert calls.index(("POST", "/api/share/terms")) < calls.index(("POST", "/api/share"))


def test_share_attaches_without_stopping_and_stop_and_new_link(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(cli, "ensure_server", lambda: None)
    monkeypatch.setattr(cli, "_health", lambda: "ok")
    monkeypatch.setattr(cli, "_api", _share_api(calls, sharing=True))
    monkeypatch.setattr(cli, "_watch_share", _interrupt)
    cli.share(action=None, new_link=False)  # started elsewhere (the UI): Ctrl+C leaves it running
    assert ("POST", "/api/share") not in calls and ("DELETE", "/api/share") not in calls
    assert runner.invoke(cli.app, ["share", "--new-link"]).exit_code == 0 and calls[-1] == ("POST", "/api/share/link")
    assert runner.invoke(cli.app, ["share", "stop"]).output.strip() == "sharing stopped" and calls[-1] == ("DELETE", "/api/share")
    assert runner.invoke(cli.app, ["share", "go"]).exit_code == 2


def test_share_refuses_a_short_password(monkeypatch):
    monkeypatch.setattr(cli, "ensure_server", lambda: None)
    monkeypatch.setattr(cli, "_api", _share_api([], strong=False))
    r = runner.invoke(cli.app, ["share"])
    assert r.exit_code == 1 and "12+ characters" in r.output


def test_close_only_own_sub_agents(monkeypatch):
    sessions = [{"id": "me1", "nick": "Ada", "parent": None}, {"id": "kid1", "nick": "Maya", "parent": "me1"},
                {"id": "user1", "nick": "Omar", "parent": None}]
    calls = []

    def api(path, method="GET", payload=None):
        calls.append((method, path))
        if path == "/api/sessions":
            return {"sessions": sessions}
        if path.startswith("/api/handoffs"):
            return {"handoffs": [{"id": "h1"}] if "kid1" in path and calls.count(("GET", path)) == 1 else []}
        return {}

    monkeypatch.setattr(cli, "_api", api)
    monkeypatch.setenv("SHELLDECK_SESSION_ID", "me1")
    assert "not your sub-agent" in runner.invoke(cli.app, ["close", "Omar"]).output
    assert "your own terminal" in runner.invoke(cli.app, ["close", "Ada"]).output
    r = runner.invoke(cli.app, ["close", "Maya"])  # hand-off h1 still open
    assert r.exit_code == 1 and "h1" in r.output and ("DELETE", "/api/sessions/kid1") not in calls
    assert runner.invoke(cli.app, ["close", "Maya"]).output.strip() == "closed Maya"
    assert ("DELETE", "/api/sessions/kid1") in calls
    monkeypatch.delenv("SHELLDECK_SESSION_ID")  # the user, outside shelldeck: any terminal
    assert runner.invoke(cli.app, ["close", "Omar"]).output.strip() == "closed Omar"
